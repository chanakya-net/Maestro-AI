FROM node:22-bookworm-slim

WORKDIR /app

COPY apps/control/package*.json ./
RUN npm install

COPY apps/control ./
RUN npm run build

ENV NODE_ENV=production \
    HOST=127.0.0.1 \
    PORT=3000 \
    AI_SKILLS_RUNNER_IMAGE=ai-skills-runner:latest

EXPOSE 3000

CMD ["npm", "start"]
