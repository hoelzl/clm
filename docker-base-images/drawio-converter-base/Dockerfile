FROM python:3.11-slim

WORKDIR /app
# Install drawio-desktop and dependencies
COPY ./drawio-amd64-24.7.5.deb .
COPY ./ArchitectsDaughter-Regular.ttf /usr/local/share/fonts/
COPY ./requirements.txt .

RUN apt-get update && apt-get install -y \
        dbus-x11 \
        nodejs \
        npm \
        libasound2 \
        libgbm1 \
        xvfb \
        x11-xkb-utils \
        xfonts-75dpi \
        xfonts-100dpi \
        fonts-liberation \
        fonts-noto \
        fonts-noto-cjk &&\
    apt-get -y -f install ./drawio-amd64-24.7.5.deb &&\
    rm drawio-amd64-24.7.5.deb &&\
    apt-get clean &&\
    rm -rf /var/lib/apt/lists/* &&\
    npm install -g svgo &&\
    fc-cache -f -v &&\
    pip install --no-cache-dir -r requirements.txt
