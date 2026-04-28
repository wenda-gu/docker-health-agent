FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/docker-health-agent

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

RUN mkdir -p /config /state

CMD ["python", "/opt/docker-health-agent/agent.py", "--config", "/config/config.yaml", "--state-file", "/state/state.json"]
