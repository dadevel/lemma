package main

import (
	//"context"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strconv"
	"strings"

	"github.com/aws/aws-lambda-go/events"
	"github.com/aws/aws-lambda-go/lambda"
)

type ExecutionSpec struct {
	Command []string `json:"command"`
	Timeout int      `json:"timeout"`
}

type ConfigSpec struct {
	ApiKey         string
	DefaultTimeout int
}

func configFromEnv() *ConfigSpec {
	apiKey := os.Getenv("LEMMA_API_KEY")
	if apiKey == "" {
		os.Exit(2)
	}
	timeout, err := strconv.Atoi(os.Getenv("LEMMA_TIMEOUT"))
	if err != nil {
		os.Exit(3)
	}
	return &ConfigSpec{
		ApiKey:         "bearer " + apiKey,
		DefaultTimeout: timeout,
	}
}

func validateAuthentication(cfg *ConfigSpec, req *events.LambdaFunctionURLRequest) error {
	str := strings.ToLower(req.Headers["authorization"])
	if subtle.ConstantTimeCompare([]byte(str), []byte(cfg.ApiKey)) != 1 {
		return errors.New("go away")
	}
	return nil
}

func decodeQueryParams(cfg *ConfigSpec, req *events.LambdaFunctionURLRequest) (*ExecutionSpec, error) {
	str := req.QueryStringParameters["exec"]
	var spec ExecutionSpec
	err := json.Unmarshal([]byte(str), &spec)
	if err != nil {
		return nil, err
	}
	if spec.Timeout == 0 {
		spec.Timeout = cfg.DefaultTimeout
	} else if spec.Timeout < 0 || spec.Timeout > cfg.DefaultTimeout {
		return nil, errors.New("timeout out of range")
	}
	if len(spec.Command) < 1 {
		return nil, errors.New("command missing")
	}
	return &spec, nil
}

func decodeBody(req *events.LambdaFunctionURLRequest) ([]byte, error) {
	if req.IsBase64Encoded {
		inputData, err := base64.StdEncoding.DecodeString(req.Body)
		return inputData, err
	} else {
		inputData := []byte(req.Body)
		return inputData, nil
	}
}

func handler(cfg *ConfigSpec, req *events.LambdaFunctionURLRequest) (*events.LambdaFunctionURLStreamingResponse, error) {
	err := validateAuthentication(cfg, req)
	if err != nil {
		return &events.LambdaFunctionURLStreamingResponse{
			StatusCode: 403,
			Body:       strings.NewReader(fmt.Sprintf("error: validateAuthentication: %v", err)),
		}, nil
	}

	execSpec, err := decodeQueryParams(cfg, req)
	if err != nil {
		return &events.LambdaFunctionURLStreamingResponse{
			StatusCode: 400,
			Body:       strings.NewReader(fmt.Sprintf("error: decodeQueryParams: %v", err)),
		}, nil
	}

	inputData, err := decodeBody(req)
	if err != nil {
		return &events.LambdaFunctionURLStreamingResponse{
			StatusCode: 500,
			Body:       strings.NewReader(fmt.Sprintf("error: decodeBody: %v", err)),
		}, nil
	}

	// TODO: cancel command after timeout
	//fmt.Printf("timeout=%d\n", execSpec.Timeout)
	//ctx, cancel := context.WithTimeout(context.Background(), time.Duration(execSpec.Timeout)*time.Second)
	//defer cancel()
	//cmd := exec.CommandContext(ctx, execSpec.Command[0], execSpec.Command[1:]...)
	cmd := exec.Command(execSpec.Command[0], execSpec.Command[1:]...)

	inputWriter, err := cmd.StdinPipe()
	if err != nil {
		return &events.LambdaFunctionURLStreamingResponse{
			StatusCode: 500,
			Body:       strings.NewReader(fmt.Sprintf("error: cmd.StdinPipe: %v", err)),
		}, nil
	}
	outputReader, err := cmd.StdoutPipe()
	if err != nil {
		return &events.LambdaFunctionURLStreamingResponse{
			StatusCode: 500,
			Body:       strings.NewReader(fmt.Sprintf("error: cmd.StdoutPipe: %v", err)),
		}, nil
	}
	cmd.Stderr = cmd.Stdout

	pipeReader, pipeWriter := io.Pipe()

	err = cmd.Start()
	if err != nil {
		return &events.LambdaFunctionURLStreamingResponse{
			StatusCode: 500,
			Body:       strings.NewReader(fmt.Sprintf("error: cmd.Start: %v", err)),
		}, nil
	}

	go func() {
		inputWriter.Write(inputData)
		inputWriter.Close()
	}()

	go func() {
		for {
			buf := make([]byte, 128)
			n, err := outputReader.Read(buf)
			if n > 0 {
				pipeWriter.Write(buf)
			}
			if err == io.EOF {
				break
			} else if err != nil {
				fmt.Printf("error: outputReader.Read %v\n", err)
				break
			}
		}
		err := cmd.Wait()
		if err != nil {
			exitError, ok := err.(*exec.ExitError)
			exitCode := -1
			if ok {
				exitCode = exitError.ExitCode()
			}
			fmt.Fprintf(pipeWriter, "exit code %d\n", exitCode)
		}
		pipeWriter.Close()
	}()

	return &events.LambdaFunctionURLStreamingResponse{
		StatusCode: 200,
		Headers: map[string]string{
			"Content-Type": "text/plain",
		},
		Body: pipeReader,
	}, nil
}

func main() {
	cfg := configFromEnv()
	if cfg == nil {
		os.Exit(4)
	}
	lambda.Start(func(req *events.LambdaFunctionURLRequest) (*events.LambdaFunctionURLStreamingResponse, error) {
		return handler(cfg, req)
	})
}
