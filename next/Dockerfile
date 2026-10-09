FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
 && git config --system user.name "EmaraAI Next" && git config --system user.email "next@emaraai.local"
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir /app
ENV EMARAAI_NODE_DATA_DIR=/data EMARAAI_NODE_HOST=0.0.0.0
VOLUME /data
EXPOSE 8810
CMD ["emaraai-next", "serve"]
