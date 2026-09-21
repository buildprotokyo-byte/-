"""IfcOpenShell の containment API による空間階層と、その矛盾検出のテスト。"""

from __future__ import annotations

import ifcopenshell
import pytest

from axes.absolute_rule_axis.ifc_containment import (
    ElementReading,
    SpatialModel,
    audit,
    build_from_reading,
)

FOUR_ROOMS = ["室1", "室2", "室3", "室4"]


def _complete_model(**kwargs) -> SpatialModel:
    """矛盾のない 4 室の読み取り結果。"""
    elements = [
        ElementReading("外壁-上", "wall", "1階"),
        ElementReading("外壁-下", "wall", "1階"),
        ElementReading("外壁-左", "wall", "1階"),
        ElementReading("外壁-右", "wall", "1階"),
        ElementReading("間仕切-縦", "wall", "1階"),
        ElementReading("間仕切-横", "wall", "1階"),
        *[ElementReading(f"戸{i}", "door", room) for i, room in enumerate(FOUR_ROOMS, 1)],
        *[ElementReading(f"窓{i}", "window", room) for i, room in enumerate(FOUR_ROOMS, 1)],
    ]
    return build_from_reading(FOUR_ROOMS, elements, **kwargs)


# --- 階層の構築 -------------------------------------------------------------


def test_hierarchy_is_project_site_building_storey_space() -> None:
    model = _complete_model()
    space = model.spaces["室1"]
    storey = SpatialModel.parent_of(space)
    building = SpatialModel.parent_of(storey)
    site = SpatialModel.parent_of(building)
    project = SpatialModel.parent_of(site)

    assert storey.is_a("IfcBuildingStorey")
    assert building.is_a("IfcBuilding")
    assert site.is_a("IfcSite")
    assert project.is_a("IfcProject")


def test_elements_are_contained_in_the_expected_space() -> None:
    model = _complete_model()
    assert SpatialModel.container_of(model.elements["戸1"]).Name == "室1"
    assert SpatialModel.container_of(model.elements["外壁-上"]).Name == "1階"


def test_reading_kinds_map_to_ifc_classes() -> None:
    model = _complete_model()
    assert model.elements["外壁-上"].is_a("IfcWall")
    assert model.elements["戸1"].is_a("IfcDoor")
    assert model.elements["窓1"].is_a("IfcWindow")


def test_contents_of_a_space_lists_its_elements() -> None:
    model = _complete_model()
    names = {e.Name for e in model.contents_of(model.spaces["室1"])}
    assert names == {"戸1", "窓1"}


def test_the_model_is_a_readable_ifc_file(tmp_path) -> None:
    """標準フォーマットとして書き出して読み直せること。"""
    path = tmp_path / "plan.ifc"
    _complete_model().save(str(path))

    reopened = ifcopenshell.open(str(path))
    assert len(reopened.by_type("IfcProject")) == 1
    assert len(reopened.by_type("IfcBuildingStorey")) == 1
    assert len(reopened.by_type("IfcSpace")) == 4
    assert len(reopened.by_type("IfcDoor")) == 4
    assert len(reopened.by_type("IfcWall")) == 6


# --- 矛盾検出 ---------------------------------------------------------------


def test_a_complete_reading_is_consistent() -> None:
    report = audit(_complete_model())
    assert report.is_consistent, report.summary()


def test_orphan_element_is_detected() -> None:
    """どの空間にも属していない要素を検出する。"""
    elements = [
        ElementReading("外壁-上", "wall", "1階"),
        ElementReading("所属不明の壁", "wall", None),
        *[ElementReading(f"戸{i}", "door", room) for i, room in enumerate(FOUR_ROOMS, 1)],
    ]
    report = audit(build_from_reading(FOUR_ROOMS, elements))
    orphans = report.of_kind("orphan_element")
    assert [f.subject for f in orphans] == ["所属不明の壁"]


def test_orphan_space_is_detected() -> None:
    report = audit(_complete_model(orphan_rooms=["室3"]))
    assert [f.subject for f in report.of_kind("orphan_space")] == ["室3"]


def test_space_without_a_door_is_detected() -> None:
    """出入口のない部屋は、建築上あり得ないので矛盾として上がる。"""
    elements = [
        ElementReading("外壁-上", "wall", "1階"),
        ElementReading("戸1", "door", "室1"),
        ElementReading("戸2", "door", "室2"),
        ElementReading("戸3", "door", "室3"),
        # 室4 の戸を読み落とした
    ]
    report = audit(build_from_reading(FOUR_ROOMS, elements))
    assert [f.subject for f in report.of_kind("space_without_door")] == ["室4"]


def test_door_contained_in_a_storey_instead_of_a_space_is_detected() -> None:
    """戸が階にしか属していないと、どの部屋の出入口か決まらない。"""
    elements = [
        ElementReading(f"戸{i}", "door", room) for i, room in enumerate(FOUR_ROOMS, 1)
    ]
    elements.append(ElementReading("所属が階どまりの戸", "door", "1階"))
    report = audit(build_from_reading(FOUR_ROOMS, elements))
    assert [f.subject for f in report.of_kind("door_without_space")] == ["所属が階どまりの戸"]


def test_empty_storey_is_detected() -> None:
    model = build_from_reading(rooms=[], elements=[])
    assert [f.subject for f in audit(model).of_kind("empty_storey")] == ["1階"]


def test_an_orphan_space_is_not_also_reported_for_missing_doors() -> None:
    """階に属していない部屋は orphan_space 1 件だけを上げる(二重計上しない)。"""
    report = audit(build_from_reading(["室1"], [], orphan_rooms=["室1"]))
    assert report.summary() == {"orphan_space": 1, "empty_storey": 1}


def test_findings_are_grouped_by_kind() -> None:
    elements = [ElementReading("浮いた壁", "wall", None)]
    report = audit(build_from_reading(["室1"], elements, orphan_rooms=["室1"]))
    summary = report.summary()
    assert summary["orphan_element"] == 1
    assert summary["orphan_space"] == 1
    assert not report.is_consistent


# --- 入力の検証 -------------------------------------------------------------


def test_unknown_container_raises() -> None:
    with pytest.raises(KeyError):
        build_from_reading(["室1"], [ElementReading("戸", "door", "存在しない部屋")])


def test_space_without_a_window_is_detected() -> None:
    """採光開口のない居室は、建築基準法の採光規定に照らして矛盾になる。"""
    elements = [
        *[ElementReading(f"戸{i}", "door", room) for i, room in enumerate(FOUR_ROOMS, 1)],
        ElementReading("窓1", "window", "室1"),
        ElementReading("窓2", "window", "室2"),
        ElementReading("窓3", "window", "室3"),
        # 室4 の窓を読み落とした
    ]
    report = audit(build_from_reading(FOUR_ROOMS, elements))
    assert [f.subject for f in report.of_kind("space_without_window")] == ["室4"]


def test_window_check_can_be_switched_off_for_non_habitable_rooms() -> None:
    elements = [ElementReading("戸1", "door", "室1")]
    model = build_from_reading(["室1"], elements)
    assert audit(model).of_kind("space_without_window")
    assert not audit(model, require_window=False).of_kind("space_without_window")


def test_a_window_contained_in_a_storey_does_not_serve_any_room() -> None:
    """窓が階どまりだと、どの部屋の採光開口か決まらないので室側は未充足のまま。"""
    elements = [
        ElementReading("戸1", "door", "室1"),
        ElementReading("階どまりの窓", "window", "1階"),
    ]
    report = audit(build_from_reading(["室1"], elements))
    assert [f.subject for f in report.of_kind("space_without_window")] == ["室1"]
