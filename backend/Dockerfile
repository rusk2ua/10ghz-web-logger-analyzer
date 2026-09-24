# Lambda container image for the /api/process endpoint.
# Built and pushed to ECR automatically by `sam build` / `sam deploy`.
FROM public.ecr.aws/lambda/python:3.12

ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
