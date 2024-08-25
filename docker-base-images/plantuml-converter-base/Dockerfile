FROM python:3.11-slim

WORKDIR /app

# Download PlantUML jar
COPY ./plantuml-1.2024.6.jar ./plantuml.jar
COPY ./requirements.txt .

# Install Java (required for PlantUML)
RUN apt-get update &&\
    apt-get install -y \
        default-jre dbus \
        graphviz &&\
    apt-get clean &&\
    rm -rf /var/lib/apt/lists/* &&\
    pip install --no-cache-dir -r requirements.txt \
