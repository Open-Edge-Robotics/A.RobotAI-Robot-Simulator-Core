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

# requirements.txt 복사 및 설치
COPY requirements.txt /robot-simulator/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /robot-simulator/requirements.txt

# src 디렉토리 복사
COPY src /robot-simulator/src

# 필요한 __init__.py 파일들 생성
RUN find /robot-simulator/src -type d -exec touch {}/__init__.py \;

# 환경 변수 설정
ENV PYTHONPATH=/robot-simulator/src

# 작업 디렉토리 설정
WORKDIR /robot-simulator/src

# 서버 시작 명령어
CMD ["bash", "-c", "source /opt/ros/humble/setup.bash && uvicorn main:app --host 0.0.0.0 --port 8000"]

