FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 默认跑日报模式（需先配好 config.yaml 和挂载数据）
CMD ["python", "skills/supply-chain-monitor/monitor.py", "--mode", "daily"]
