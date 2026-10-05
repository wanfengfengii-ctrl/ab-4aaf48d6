FROM python:3.11-slim

WORKDIR /app

# verify 阶段需要 pytest；setuptools/wheel 用于镜像内项目构建
RUN pip install --no-cache-dir pytest "setuptools>=68" wheel

COPY . .

# 镜像构建即完成一次项目安装；verify 服务会再次执行以验证镜像内可构建
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8080 9100

HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=5 \
    CMD python scripts/healthcheck.py || exit 1

CMD ["python", "-m", "app.main"]
