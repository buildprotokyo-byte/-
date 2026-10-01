# 機械の段だけを動かす入れ物(K-62 の追記 1・4)。AI は呼ばない。
# 置き場所は決め打ちしない: 図面とキャッシュは実行するときに外から渡す。
#
#   docker build -f docker/machine.Dockerfile -t sekisan-machine .
#   docker run --rm -v <図面のフォルダ>:/data -v <キャッシュの置き場所>:/cache \
#       sekisan-machine prepare /data/図面.pdf --ocr
#
# 注意: この入れ物はクラウドの作業環境では組み立てて試していない(docker が無い)。
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libcairo2 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-ocr.txt ./
RUN pip install --no-cache-dir -r requirements.txt && (pip install --no-cache-dir -r requirements-ocr.txt || true)
COPY . .
ENV DRAFT_CACHE_DIR=/cache PYTHONUTF8=1
ENTRYPOINT ["python", "-m", "draft.machine"]
