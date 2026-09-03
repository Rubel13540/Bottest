FROM node:18-slim

# Install Tor and browser dependencies
RUN apt-get update && apt-get install -y \
    tor \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

EXPOSE 3000
CMD ["sh", "-c", "tor & npm start"]
