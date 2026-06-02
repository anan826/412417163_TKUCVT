# ---------- builder stage ----------
FROM python:3.12 AS builder
WORKDIR /build
COPY app/requirements.txt .
# 把相依安裝到獨立 prefix，之後整包搬走
# 用 --prefix 而不是 --target：--prefix 會同時產生 bin/ 與 lib/pythonX.Y/site-packages/，
# console script（例如 flask、gunicorn）才不會漏掉。
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------- runtime stage ----------
FROM python:3.12-slim AS runtime
WORKDIR /app

# 把 /install 底下的 bin 與 site-packages 一起搬到 runtime 的對應位置
COPY --from=builder /install/bin /usr/local/bin
COPY --from=builder /install/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY app/ .

# 切成非 root
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 80
ENTRYPOINT ["python"]
CMD ["app.py"]
