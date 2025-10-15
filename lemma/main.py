from argparse import ArgumentParser, BooleanOptionalAction
from datetime import datetime, timezone
from typing import Generator
import argparse
import json
import os
import secrets
import shlex
import sys
import time

import boto3
import requests


def main() -> None:
    entrypoint = ArgumentParser()
    parsers = entrypoint.add_subparsers(dest='action')

    parser = parsers.add_parser('create')
    parser.add_argument('-r', '--region', default=os.environ.get('LEMMA_REGION', os.environ.get('AWS_DEFAULT_REGION')), metavar='REGION')
    parser.add_argument('--image', default=os.environ.get('LEMMA_IMAGE'), metavar='URI')
    parser.add_argument('--role', default=os.environ.get('LEMMA_ROLE'), metavar='ARN')
    parser.add_argument('--memory', type=int, default=128, metavar='MB')
    parser.add_argument('--storage', type=int, default=512, metavar='MB')
    parser.add_argument('-t', '--timeout', type=int, default=int(os.environ.get('LEMMA_TIMEOUT', 60 * 5)), metavar='SECONDS')
    parser.add_argument('-e', '--env', action='append', default=[], metavar='KEY=VALUE')
    parser.add_argument('--export', action=BooleanOptionalAction)

    parser = parsers.add_parser('delete')
    parser.add_argument('-r', '--region', default=os.environ.get('LEMMA_REGION', os.environ.get('AWS_DEFAULT_REGION')), metavar='REGION')
    parser.add_argument('name', nargs='?', default=os.environ.get('LEMMA_INSTANCE'))

    parser = parsers.add_parser('invoke')
    parser.add_argument('-r', '--region', default=os.environ.get('LEMMA_REGION', os.environ.get('AWS_DEFAULT_REGION')), metavar='REGION')
    parser.add_argument('-u', '--url', default=os.environ.get('LEMMA_URL'))
    parser.add_argument('-k', '--api-key', default=os.environ.get('LEMMA_API_KEY'), metavar='STRING')
    parser.add_argument('-t', '--timeout', type=int, default=None)
    parser.add_argument('-s', '--stdin', action=BooleanOptionalAction)
    parser.add_argument('command', nargs=argparse.REMAINDER)

    parser = parsers.add_parser('list')
    parser.add_argument('-r', '--region', default=os.environ.get('LEMMA_REGION', os.environ.get('AWS_DEFAULT_REGION')), metavar='REGION')

    parser = parsers.add_parser('run')
    parser.add_argument('-r', '--region', default=os.environ.get('LEMMA_REGION', os.environ.get('AWS_DEFAULT_REGION')), metavar='REGION')
    parser.add_argument('--image', default=os.environ.get('LEMMA_IMAGE'), metavar='URI')
    parser.add_argument('--role', default=os.environ.get('LEMMA_ROLE'), metavar='ARN')
    parser.add_argument('--memory', type=int, default=128, metavar='MB')
    parser.add_argument('--storage', type=int, default=512, metavar='MB')
    parser.add_argument('-t', '--timeout', type=int, default=int(os.environ.get('LEMMA_TIMEOUT', 60 * 5)), metavar='SECONDS')
    parser.add_argument('-e', '--env', action='append', default=[], metavar='KEY=VALUE')
    parser.add_argument('-s', '--stdin', action=BooleanOptionalAction)
    parser.add_argument('command', nargs=argparse.REMAINDER)

    opts = entrypoint.parse_args()

    if not opts.region:
        raise UsageError('region missing, please specify --region, $LEMMA_REGION or $AWS_DEFAULT_REGION')
    client = boto3.client('lambda', region_name=opts.region)

    match opts.action:
        case 'create':
            api_key = generate_random_key()
            name = generate_random_name()
            url = create_lambda(client, name, api_key, opts.image, opts.role, translate_env(opts.env), opts.memory, opts.storage, opts.timeout)
            print(format_env(dict(LEMMA_INSTANCE=name, LEMMA_URL=url, LEMMA_API_KEY=api_key), export=opts.export))
        case 'delete':
            delete_lambda(client, opts.name)
        case 'invoke':
            for chunk in invoke_lambda(opts.url, opts.api_key, opts.command, opts.stdin, opts.timeout):
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        case 'list':
            for line in list_lambdas(client):
                print(line)
        case 'run':
            api_key = generate_random_key()
            name = generate_random_name()
            url = create_lambda(client, name, api_key, opts.image, opts.role, translate_env(opts.env), opts.memory, opts.storage, opts.timeout)
            print(f'created lambda {name}')
            try:
                print('invoking lambda')
                for chunk in invoke_lambda(url, api_key, opts.command, opts.stdin, opts.timeout):
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
            finally:
                print('deleting lambda')
                delete_lambda(client, name)


def create_lambda(client, name: str, api_key: str, image: str|None, role: str|None, env: dict[str, str], memory_size: int, storage_size: int, timeout: int) -> str:
    if not image:
        raise UsageError('image missing, please specify --image or set $LEMMA_IMAGE')
    if not role:
        raise UsageError('role missing, please specify --role or set $LEMMA_ROLE')
    print('creating lambda', file=sys.stderr)
    response = client.create_function(
        FunctionName=name,
        Role=role,
        Code=dict(ImageUri=image),
        Timeout=timeout,
        MemorySize=memory_size,
        EphemeralStorage=dict(
            Size=storage_size,
        ),
        Publish=True,
        PackageType='Image',
        Environment=dict(
            Variables=env|dict(
                LEMMA_INSTANCE=name,
                LEMMA_API_KEY=api_key,
                LEMMA_TIMEOUT=str(timeout),
            ),
        ),
        Architectures=['x86_64'],
        LoggingConfig=dict(
            LogFormat='JSON',
            ApplicationLogLevel='TRACE',  # DEBUG, INFO, WARN, ERROR
            SystemLogLevel='DEBUG',  # DEBUG, INFO, WARN
        ),
    )
    while True:
        response = client.get_function(FunctionName=name)
        response = response['Configuration']
        if response['State'] != 'Pending':
            break
        print(f'deployment status: {response['StateReason']}')
        time.sleep(2)
    if response['State'] == 'Failed':
        raise RuntimeError(f'creation failed: {response['StateReason']}')

    print('creating lambda url', file=sys.stderr)
    response = client.create_function_url_config(
        FunctionName=name,
        AuthType='NONE',
        InvokeMode='RESPONSE_STREAM',
    )

    print('assigning lambda permissions', file=sys.stderr)
    client.add_permission(
        FunctionName=name,
        StatementId='FunctionURLAllowPublicAccess',
        Principal='*',
        Action='lambda:InvokeFunctionUrl',
        FunctionUrlAuthType='NONE',
    )
    client.add_permission(
        FunctionName=name,
        StatementId='FunctionURLAllowInvokeAction',
        Principal='*',
        Action='lambda:InvokeFunction',
        InvokedViaFunctionUrl=True,
    )

    return response['FunctionUrl']


def delete_lambda(client, name: str|None) -> None:
    if not name:
        raise UsageError('name missing, please specify positional argument or set $LEMMA_INSTANCE')
    client.delete_function(FunctionName=name)


def list_lambdas(client) -> Generator[str]:
    response = client.list_functions()
    for function in response['Functions']:
        if function['FunctionName'].startswith('lemma-'):
            yield function['FunctionName']
    while marker := response.get('NextMarker'):
        response = client.list_functions(Marker=marker)
        for function in response['Functions']:
            if function['FunctionName'].startswith('lemma-'):
                yield function['FunctionName']


def invoke_lambda(url: str|None, api_key: str|None, command: list[str]|None, stdin: bool, timeout: int) -> Generator[bytes]:
    if not url:
        raise UsageError('url missing, please specify --url or set $LEMMA_URL')
    if not command:
        raise UsageError('command missing, please specify positional argument(s)')
    if not api_key:
        raise UsageError('api key missing, please specify --api-key or set $LEMMA_API_KEY')
    response = requests.post(
        url,
        headers={'Authorization': f'Bearer {api_key}'},
        params=dict(exec=json.dumps(
            dict(
                command=command,
                timeout=timeout,
            ),
            separators=(',', ':'),
        )),
        data=sys.stdin.buffer.read() if stdin else None,
        stream=True,
    )
    response.raise_for_status()
    for chunk in response.iter_content(chunk_size=None):
        yield chunk


def translate_env(data: list[str]) -> dict[str, str]:
    result = {}
    for item in data:
        if '=' in item:
            key, value = item.split('=', maxsplit=1)
        else:
            key = item
            value = os.environ.get(item, '')
        result[key] = value
    return result


def format_env(data: dict[str, str], export: bool = False) -> str:
    prefix = 'export ' if export else ''
    return '\n'.join(f'{prefix}{shlex.quote(key)}={shlex.quote(value)}' for key, value in data.items())


def generate_random_name() -> str:
    timestamp = datetime.now(tz=timezone.utc).strftime('%Y%m%d')
    random = secrets.token_hex(8)
    return f'lemma-{timestamp}-{random}'


def generate_random_key() -> str:
    return secrets.token_hex(32)


class UsageError(Exception):
    pass


if __name__ == '__main__':
    main()
