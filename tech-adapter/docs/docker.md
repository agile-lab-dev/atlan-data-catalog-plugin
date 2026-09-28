# Running with Docker
You can use the below instructions if you want to run your app as a docker image.
### Build Docker image
```
docker build -t atlan-data-catalog-plugin .
```
### Container execution
At this point, we can run the `image` as a container via the `run` command and the name associated with the image during the build:
```
docker run -d --name atlan-data-catalog-plugin-container -p 5002:5002 atlan-data-catalog-plugin
```
#### OpenTelemetry activation
To enable automatic instrumentation, you can pass the parameter `open_telemetry_activation` to the `entrypoint` of the container in this way:
```
docker run -d --name atlan-data-catalog-plugin-container -p 5002:5002 atlan-data-catalog-plugin open_telemetry_activation
```

You need to set some `environment variables` while running the application with OpenTelemetry and send collected data to an `Observability backend`. Referring to [SignOz](./opentelemetry.md), you can do so with the following commands at the terminal:
```
# If you have your SigNoz IP Address, replace <IP of SigNoz> with your IP Address.

docker run -d --name atlan-data-catalog-plugin-container \
-e OTEL_METRICS_EXPORTER='none' \
-e OTEL_RESOURCE_ATTRIBUTES='service.name=AtlanDataCatalogPlugin' \
-e OTEL_EXPORTER_OTLP_ENDPOINT='http://<IP of SigNoz>:4317' \
-p 5002:5002 atlan-data-catalog-plugin open_telemetry_activation
```
If you are running SigNoz in your local host then you can replace `<IP of SigNoz>` with `localhost` and the final command will look like below:
```
docker run -d --name atlan-data-catalog-plugin-container \
-e OTEL_METRICS_EXPORTER='none' \
-e OTEL_RESOURCE_ATTRIBUTES='service.name=AtlanDataCatalogPlugin' \
-e OTEL_EXPORTER_OTLP_ENDPOINT='http:localhost:4317' \
-p 5002:5002 atlan-data-catalog-plugin open_telemetry_activation
```
