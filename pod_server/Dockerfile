FROM ros:humble

RUN apt update && \
    apt install locales && \
    locale-gen en_US en_US.UTF-8 && \
    update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

RUN apt install software-properties-common -y
RUN add-apt-repository universe

RUN apt install ros-humble-desktop -y
RUN apt install ros-dev-tools -y

RUN apt install python3-pip -y
RUN apt install python3.10 -y

COPY requirements.txt /pod_server/requirements.txt

ENV PYTHONPATH=/pod_server

RUN pip install --no-cache-dir --upgrade -r /pod_server/requirements.txt

COPY src /pod_server/src

CMD ["bash", "-c", "echo source /opt/ros/humble/setup.bash >> ~/.bashrc && source ~/.bashrc && uvicorn pod_server.src.main:app --host 0.0.0.0 --port 8002"]
