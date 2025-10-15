FROM docker.io/library/golang:alpine AS build
WORKDIR /build
COPY ./go.mod ./go.sum .
COPY ./lambda.go .
RUN go build -tags lambda.norpc ./lambda.go

FROM docker.io/library/alpine:latest
RUN apk add --no-cache nmap nmap-scripts
COPY --from=build /build/lambda /bootstrap
WORKDIR /tmp
ENTRYPOINT ["/bootstrap"]
CMD []
