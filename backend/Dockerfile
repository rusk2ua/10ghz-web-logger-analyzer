# Lambda container image: analyses (10ghz-log-analyzer), maps (grid-mapper),
# usage recording and the stats dashboard job.
# Built and pushed to ECR automatically by `sam build` / `sam deploy`.
FROM public.ecr.aws/lambda/python:3.12

# Natural Earth data for grid-mapper is baked into /opt/cartopy below; /tmp is
# the only writable place in Lambda for anything cartopy would otherwise download.
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1 \
    CARTOPY_DATA_DIR=/opt/cartopy \
    XDG_DATA_HOME=/tmp

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Coastlines, land, ocean, lakes and borders (~36 MB), so map jobs never
# download them at run time.
COPY fetch_natural_earth.py /tmp/
RUN env -u CARTOPY_DATA_DIR python /tmp/fetch_natural_earth.py /opt/cartopy \
    && rm /tmp/fetch_natural_earth.py \
    && chmod -R a+rX /opt/cartopy

COPY app/ ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
