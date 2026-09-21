"""絶対ルール軸: IfcOpenShell の containment API による空間階層の構築と矛盾検出。

`6軸_実装詳細設計書.md` の「`axes/absolute_rule_axis/ifc_containment.py`:IfcOpenShell の
containment API を使った空間階層検証」に対応します。

v8 設計の Layer 3(順序推論層)は「空間包含順序」を前提にしています。IFC(ISO 16739)は
まさにその包含順序を国際標準として定義しているので、読み取った要素をここに載せるだけで
「壁は階に属する」「部屋は階に属する」といった構造が、自前のデータ構造ではなく標準の形
で表現できます。

構造
----
IFC の空間分解は 2 種類の関係で組み立てます。

- **aggregation**(``aggregate.assign_object``)… 空間同士の入れ子。
  Project → Site → Building → Storey → Space
- **containment**(``spatial.assign_container``)… 空間に物理要素を入れる。
  Storey / Space → Wall, Door, Window, ...

このモジュールは、読み取り結果(部屋・壁・開き戸・窓)を受け取ってこの階層を実際に
構築し、``audit()`` で階層上の異常を洗い出します。

矛盾検出
--------
``audit()`` が返すのは「どの要素が空間階層のどこに収まっていないか」です。これは
v8 の「積集合が空集合になる = 軸間の矛盾」と同じ役割を、空間の包含関係の側で果たします。
検出するのは次の 5 種類です。

1. ``orphan_element``       … どの空間にも属していない物理要素
2. ``orphan_space``         … どの階にも属していない部屋
3. ``empty_storey``         … 部屋も要素も持たない階
4. ``door_without_space``   … 部屋に属していない開き戸(壁だけに属している状態)
5. ``space_without_door``   … 出入口を 1 つも持たない部屋
6. ``space_without_window`` … 採光のための開口部を 1 つも持たない居室

1〜3 は IFC の構造そのものの破れで、読み取り漏れを確実に示します。4・5 は
「部屋には出入口がある」という建築上の前提に対する違反で、絶対ルール軸が
本来検出したい種類の矛盾です。6 は建築基準法の採光規定(居室には採光のための
開口部が必要)に対応します。**居室でない部屋(納戸・便所等)には適用できない**ため、
``require_window=False`` で切れるようにしてあります。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import ifcopenshell
import ifcopenshell.api
from ifcopenshell.api import aggregate, root, spatial

IFC_SCHEMA = "IFC4"

ElementKind = Literal["wall", "door", "window", "slab", "other"]

#: 読み取り結果の要素種別 → IFC のクラス名
IFC_CLASS_BY_KIND: dict[str, str] = {
    "wall": "IfcWall",
    "door": "IfcDoor",
    "window": "IfcWindow",
    "slab": "IfcSlab",
    "other": "IfcBuildingElementProxy",
}

FindingKind = Literal[
    "orphan_element",
    "orphan_space",
    "empty_storey",
    "door_without_space",
    "space_without_door",
    "space_without_window",
]


@dataclass(frozen=True)
class Finding:
    """空間階層の異常 1 件。"""

    kind: FindingKind
    subject: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - 表示用
        return f"[{self.kind}] {self.subject}: {self.message}"


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def is_consistent(self) -> bool:
        return not self.findings

    def of_kind(self, kind: FindingKind) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.kind] = counts.get(finding.kind, 0) + 1
        return counts


class SpatialModel:
    """IFC の空間階層を組み立てるラッパー。

    ``ifcopenshell.api.spatial.assign_container()`` と
    ``ifcopenshell.api.aggregate.assign_object()`` を、読み取り結果の語彙
    (部屋・壁・戸・窓)で呼べるようにしたものです。
    """

    def __init__(self, project_name: str = "自動積算プロジェクト") -> None:
        self.file = ifcopenshell.file(schema=IFC_SCHEMA)
        self.project = root.create_entity(self.file, ifc_class="IfcProject", name=project_name)
        self.site: ifcopenshell.entity_instance | None = None
        self.building: ifcopenshell.entity_instance | None = None
        self.storeys: dict[str, ifcopenshell.entity_instance] = {}
        self.spaces: dict[str, ifcopenshell.entity_instance] = {}
        self.elements: dict[str, ifcopenshell.entity_instance] = {}

    # -- 空間の構築 -------------------------------------------------------
    def add_site(self, name: str = "敷地") -> ifcopenshell.entity_instance:
        self.site = root.create_entity(self.file, ifc_class="IfcSite", name=name)
        aggregate.assign_object(self.file, products=[self.site], relating_object=self.project)
        return self.site

    def add_building(self, name: str = "建物") -> ifcopenshell.entity_instance:
        if self.site is None:
            self.add_site()
        self.building = root.create_entity(self.file, ifc_class="IfcBuilding", name=name)
        aggregate.assign_object(self.file, products=[self.building], relating_object=self.site)
        return self.building

    def add_storey(self, name: str) -> ifcopenshell.entity_instance:
        if self.building is None:
            self.add_building()
        storey = root.create_entity(self.file, ifc_class="IfcBuildingStorey", name=name)
        aggregate.assign_object(self.file, products=[storey], relating_object=self.building)
        self.storeys[name] = storey
        return storey

    def add_space(self, name: str, storey: str | None) -> ifcopenshell.entity_instance:
        """部屋を作る。``storey`` に None を渡すと、どの階にも属さない状態になる。

        None を許しているのは、読み取り漏れを ``audit()`` で検出できることを
        確かめるためです(異常を意図的に作れないと、検出できているか分からない)。
        """
        space = root.create_entity(self.file, ifc_class="IfcSpace", name=name)
        if storey is not None:
            aggregate.assign_object(
                self.file, products=[space], relating_object=self.storeys[storey]
            )
        self.spaces[name] = space
        return space

    # -- 物理要素の登録 ---------------------------------------------------
    def add_element(
        self,
        name: str,
        kind: ElementKind,
        container: str | None,
    ) -> ifcopenshell.entity_instance:
        """物理要素を作り、``container``(階名または部屋名)に収める。

        ``container`` に None を渡すと、どの空間にも属さない状態になります。
        """
        ifc_class = IFC_CLASS_BY_KIND.get(kind, IFC_CLASS_BY_KIND["other"])
        element = root.create_entity(self.file, ifc_class=ifc_class, name=name)
        if container is not None:
            structure = self.storeys.get(container) or self.spaces.get(container)
            if structure is None:
                raise KeyError(f"存在しない空間です: {container}")
            spatial.assign_container(
                self.file, products=[element], relating_structure=structure
            )
        self.elements[name] = element
        return element

    # -- 参照 -------------------------------------------------------------
    @staticmethod
    def container_of(element: ifcopenshell.entity_instance) -> ifcopenshell.entity_instance | None:
        """要素を収めている空間を返す(無ければ None)。"""
        for rel in getattr(element, "ContainedInStructure", None) or []:
            return rel.RelatingStructure
        return None

    @staticmethod
    def parent_of(space: ifcopenshell.entity_instance) -> ifcopenshell.entity_instance | None:
        """空間の親空間を返す(無ければ None)。"""
        for rel in getattr(space, "Decomposes", None) or []:
            return rel.RelatingObject
        return None

    def contents_of(self, structure: ifcopenshell.entity_instance) -> list[Any]:
        """空間に直接収められている物理要素の一覧。"""
        contents: list[Any] = []
        for rel in getattr(structure, "ContainsElements", None) or []:
            contents.extend(rel.RelatedElements)
        return contents

    def save(self, path: str) -> None:
        self.file.write(path)


# ---------------------------------------------------------------------------
# 読み取り結果からの構築
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ElementReading:
    """図面から読み取った物理要素 1 つ。

    ``container`` は、その要素が属すると読み取れた空間の名前。読み取れなかった
    場合は None。
    """

    name: str
    kind: ElementKind
    container: str | None


def build_from_reading(
    rooms: Iterable[str],
    elements: Iterable[ElementReading],
    *,
    storey_name: str = "1階",
    project_name: str = "自動積算プロジェクト",
    orphan_rooms: Iterable[str] = (),
) -> SpatialModel:
    """読み取り結果から Project → Site → Building → Storey → Space → Element を組む。

    ``orphan_rooms`` に入れた部屋は、意図的にどの階にも属さない状態で作ります
    (矛盾検出の動作確認用)。
    """
    model = SpatialModel(project_name=project_name)
    model.add_storey(storey_name)

    orphans = set(orphan_rooms)
    for room in rooms:
        model.add_space(room, storey=None if room in orphans else storey_name)

    for reading in elements:
        model.add_element(reading.name, reading.kind, reading.container)

    return model


# ---------------------------------------------------------------------------
# 矛盾検出
# ---------------------------------------------------------------------------


def audit(model: SpatialModel, *, require_window: bool = True) -> AuditReport:
    """空間階層の異常を洗い出す。

    ``require_window`` を False にすると、採光開口のチェック
    (``space_without_window``)を行いません。納戸・便所など、居室でない部屋を
    含むモデルではこちらを使ってください。
    """
    report = AuditReport()

    for name, element in model.elements.items():
        if SpatialModel.container_of(element) is None:
            report.findings.append(
                Finding(
                    "orphan_element",
                    name,
                    "どの空間にも属していません(読み取り漏れ、または所属の判定失敗)",
                )
            )

    for name, space in model.spaces.items():
        if SpatialModel.parent_of(space) is None:
            report.findings.append(
                Finding("orphan_space", name, "どの階にも属していません")
            )

    for name, storey in model.storeys.items():
        has_space = any(
            SpatialModel.parent_of(space) == storey for space in model.spaces.values()
        )
        if not has_space and not model.contents_of(storey):
            report.findings.append(
                Finding("empty_storey", name, "部屋も要素も 1 つも属していません")
            )

    space_entities = set(model.spaces.values())
    doors_by_space: dict[str, list[str]] = {name: [] for name in model.spaces}
    windows_by_space: dict[str, list[str]] = {name: [] for name in model.spaces}
    for name, element in model.elements.items():
        is_door = element.is_a("IfcDoor")
        is_window = element.is_a("IfcWindow")
        if not (is_door or is_window):
            continue
        container = SpatialModel.container_of(element)
        if container is None:
            continue  # orphan_element で既に報告済み
        if container not in space_entities:
            if is_door:
                report.findings.append(
                    Finding(
                        "door_without_space",
                        name,
                        f"部屋ではなく {container.is_a()} に属しています"
                        "(どの部屋の出入口かが決まっていません)",
                    )
                )
            continue
        target = doors_by_space if is_door else windows_by_space
        target[container.Name].append(name)

    for room in model.spaces:
        if SpatialModel.parent_of(model.spaces[room]) is None:
            continue  # orphan_space で既に報告済み
        if not doors_by_space[room]:
            report.findings.append(
                Finding("space_without_door", room, "出入口が 1 つもありません")
            )
        if require_window and not windows_by_space[room]:
            report.findings.append(
                Finding(
                    "space_without_window",
                    room,
                    "採光のための開口部が 1 つもありません(建築基準法の採光規定)",
                )
            )

    return report
