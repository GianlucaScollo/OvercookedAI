FROM python:3.7-buster 

ARG BUILD_ENV
ARG OVERCOOKED_BRANCH
ARG GRAPHICS

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

RUN apt-get -y update
RUN apt-get install -y libgl1-mesa-dev

# Copy over remaining files
COPY ./static ./static
COPY ./*.py ./
COPY ./graphics/$GRAPHICS ./static/js/graphics.js
COPY ./config.json ./config.json



# Set environment variables that will be used by app.py
ENV HOST 0.0.0.0
ENV PORT 5000
ENV CONF_PATH config.json

# Do the thing
EXPOSE 5000
CMD ["python", "-u", "app.py"]
