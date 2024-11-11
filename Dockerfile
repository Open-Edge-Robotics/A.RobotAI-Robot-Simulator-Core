FROM python:3.12.5

WORKDIR /robot-simulator

COPY ./requirements.txt /robot-simulator/requirements.txt

ENV PYTHONPATH=/robot-simulator

RUN pip install --no-cache-dir --upgrade -r /robot-simulator/requirements.txt

COPY ./src /robot-simulator/src

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]