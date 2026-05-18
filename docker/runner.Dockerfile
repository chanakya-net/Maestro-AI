FROM node:22-bookworm-slim

ENV HOME=/home/agent \
    NPM_CONFIG_PREFIX=/home/agent/.local \
    NPM_CONFIG_CACHE=/home/agent/.npm \
    AI_SKILLS_ROOT=/opt/ai-skills \
    PATH=/home/agent/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash \
      bubblewrap \
      ca-certificates \
      curl \
      git \
      jq \
      python3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --home-dir /home/agent --shell /bin/bash agent \
    && mkdir -p /workspace /opt/ai-skills /home/agent/.local \
    && chown -R agent:agent /home/agent /workspace

COPY assets /opt/ai-skills/assets
COPY skills /opt/ai-skills/skills
COPY install.sh /opt/ai-skills/install.sh

RUN chmod +x /opt/ai-skills/assets/run-agent.sh /opt/ai-skills/install.sh

USER agent
WORKDIR /workspace

CMD ["bash", "-lc", "sleep infinity"]
