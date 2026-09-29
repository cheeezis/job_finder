FROM python:3.13-slim

WORKDIR /app

# Dates and interview times are local German time, as entered in the review.
ENV TZ=Europe/Berlin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "run_finder.py"]