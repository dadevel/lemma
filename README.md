# Lemma

Run your favourite command-line tools on [AWS Lambda](https://aws.amazon.com/lambda/) by packaging them into a container image and streaming their output back to your terminal.

This project is based on [github.com/sleepyeinstein/lemma](https://github.com/sleepyeinstein/lemma).

## Setup

Install `lemma`.

~~~ bash
uv tool install git+https://github.com/dadevel/lemma.git@main
~~~

First create a role for the Lambda functions and note the role ARN.
The role does not require any permissions.

~~~ bash
aws iam create-role --role-name LemmaFunctionRole --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
~~~

Then deploy a Elastic Container Registry and note its domain.

~~~ bash
aws ecr create-repository --repository-name lemma
~~~

Finally build and push a suitable container image to ECR.
You should adjust the [Dockerfile](./Dockerfile) to use your Linux distro of choice and pre-install all your tools.

~~~ bash
podman build --tag 0123456789.dkr.ecr.eu-central-1.amazonaws.com/lemma:latest .
aws ecr get-login-password --region eu-central-1 | podman login 0123456789.dkr.ecr.eu-central-1.amazonaws.com --username AWS --password-stdin
podman push 0123456789.dkr.ecr.eu-central-1.amazonaws.com/lemma:latest
~~~

## Usage

> [!warning]
> It is your responsibility to comply with the [AWS Pentesting Policy](https://aws.amazon.com/security/penetration-testing/).

Deploy a temporary Lambda and invoke it once to execute a single command.
After the command exits the Lambda will be destroyed again.

~~~ bash
aws sts get-caller-identity
export LEMMA_ROLE=arn:aws:iam::0123456789:role/LemmaFunctionRole
export LEMMA_IMAGE=0123456789.dkr.ecr.eu-central-1.amazonaws.com/lemma:latest
lemma run nmap -vv -n -Pn -sT --top-ports 100 -T4 scanme.nmap.org
~~~

Manually create, invoke and delete a Lambda.

~~~ bash
eval "$(lemma create --role arn:aws:iam::0123456789:role/LemmaFunctionRole --image 0123456789.dkr.ecr.eu-central-1.amazonaws.com/lemma:latest --export)"
cat ./hosts.txt | parallel --progress -j 8 --results ./nmap-{}.xml lemma invoke nmap -vv -n -Pn -sT -p80,443 -sV -sC -oX - {}
lemma delete
xq . ./nmap-*.xml
~~~

> [!tip]
> - The maximum runtime of a Lambda is 15min. The default timeout set by Lemma is 5min.
> - Sequential invocations of the same Lambda, even with a delay of up to 15min in between them, will share the same execution environment and therefore the same source IP. However, parallel invocations will each have a different source IP.
> - AWS Lambda only supports response streaming, not request streaming. This means that when `--stdin` is used, the remote command will only be executed after the entire stdin has been received.
> - Tools that requires root or *CAP_NET_RAW* do not work on AWS Lambda.

## Development

Lemma is split into two components:

- The command-line client running on your laptop ([lemma/main.py](./lemma/main.py))
- And the server running as Lambda ([lambda.go](./lambda.go))

Start the Lambda component locally.

~~~ bash
curl -Lo ./aws-lambda-rie https://github.com/aws/aws-lambda-runtime-interface-emulator/releases/latest/download/aws-lambda-rie
chmod +x ./aws-lambda-rie
go build -tags lambda.norpc ./lambda.go && LEMMA_API_KEY=foobar LEMMA_TIMEOUT=10 ./aws-lambda-rie ./lambda
~~~

Invoke the Lambda (Lambda RIE does not emulate response streaming).

~~~ bash
curl -sS --fail-with-body http://localhost:8080/2015-03-31/functions/function/invocations -d '{"headers":{"authorization":"bearer foobar"},"QueryStringParameters":{"exec":"{\"command\":[\"cat\",\"-\"]}"},"body":"test test"}' -o-
~~~
