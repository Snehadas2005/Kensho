FROM golang:alpine

WORKDIR /repo
RUN apk add --no-cache git ca-certificates

# Allow Go to automatically fetch the exact toolchain version declared in go.mod
ENV GOTOOLCHAIN=auto

# Copy root go.mod/go.sum and all local replaced module manifests
COPY workspace/pipelines/go.mod workspace/pipelines/go.sum ./
COPY workspace/pipelines/api/go.mod workspace/pipelines/api/go.sum* ./api/
COPY workspace/pipelines/kubernetes_platform/go.mod workspace/pipelines/kubernetes_platform/go.sum* ./kubernetes_platform/
RUN go mod download

CMD ["go", "version"]