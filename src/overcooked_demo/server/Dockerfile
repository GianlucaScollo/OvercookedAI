FROM python:3.9-bookworm

ARG BUILD_ENV
ARG OVERCOOKED_BRANCH
ARG GRAPHICS
ARG AGENT_BRANCH

WORKDIR /app

# Install non-chai dependencies
COPY ./requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Install eventlet production server if production build
RUN if [ "$BUILD_ENV" = "production" ] ; then pip install eventlet ; fi

# Clone chai code
RUN git clone --recursive https://github.com/margaritaradeva/OvercookedAIGameLogic.git --branch $OVERCOOKED_BRANCH --single-branch /OvercookedAIGameLogic

# Dummy data_dir so things don't break
RUN echo "import os; DATA_DIR=os.path.abspath('.')" >>/OvercookedAIGameLogic/src/human_aware_rl/data_dir.py

# Install chai dependencies
RUN pip install -e '/OvercookedAIGameLogic[harl]'

RUN apt-get -y update && apt-get install -y \
    libgl1-mesa-dev \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Java 23.0.2
RUN wget -q \
    https://download.oracle.com/java/23/archive/jdk-23.0.2_linux-x64_bin.tar.gz \
    -O /tmp/jdk23.tar.gz \
    && mkdir -p /opt/java \
    && tar -xzf /tmp/jdk23.tar.gz -C /opt/java \
    && rm /tmp/jdk23.tar.gz

ENV JAVA_HOME=/opt/java/jdk-23.0.2
ENV PATH="${JAVA_HOME}/bin:${PATH}"

# Install Gradle 8.13
RUN wget -q \
    https://services.gradle.org/distributions/gradle-8.13-bin.zip \
    -O /tmp/gradle.zip \
    && unzip -q /tmp/gradle.zip -d /opt \
    && rm /tmp/gradle.zip

ENV PATH="/opt/gradle-8.13/bin:${PATH}"


# Clone SymbolicAIAgent
RUN git clone https://github.com/GianlucaScollo/SymbolicAIAgent.git --branch $AGENT_BRANCH --single-branch /app/SymbolicAIAgent

# Pre-build (Download Gradle dependencies at build time — best effort)
RUN cd /app/SymbolicAIAgent && gradle dependencies --no-daemon || true


# Copy over remaining files
COPY ./static ./static
COPY ./*.py ./
COPY ./graphics/$GRAPHICS ./static/js/graphics.js
COPY ./config.json ./config.json

# Set environment variables that will be used by app.py
ENV HOST=0.0.0.0
ENV PORT=5000
ENV CONF_PATH=config.json

# Do the thing
EXPOSE 5000
CMD ["python", "-u", "app.py"]
