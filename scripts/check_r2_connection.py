"""Test one disposable R2 object without importing Django or loading .env.

Run only from a trusted terminal. No arbitrary object keys are accepted, bucket
settings are never changed, and SDK/HTTP exception messages are never printed.
"""

import argparse
from dataclasses import dataclass, field
import getpass
import hashlib
import logging
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid
import warnings
from xml.etree import ElementTree


EXPECTED_BUCKET = "tambul-portal-media"
PREFIX = "_connection-tests/minutes/"
VARIABLES = (
    "MINUTES_R2_UPLOADS_ENABLED",
    "MINUTES_MAX_UPLOAD_BYTES",
    "MINUTES_R2_ACCOUNT_ID",
    "MINUTES_R2_BUCKET_NAME",
    "MINUTES_R2_ACCESS_KEY_ID",
    "MINUTES_R2_SECRET_ACCESS_KEY",
)
MAX_ANONYMOUS_BODY_BYTES = 8192
AUTHENTICATION_ERROR_CODES = frozenset({
    "AccessDenied", "Unauthorized", "MissingAuthenticationToken",
    "AuthorizationHeaderMalformed", "SignatureDoesNotMatch", "InvalidAccessKeyId",
})
SAFE_ERROR_CODES = AUTHENTICATION_ERROR_CODES | {
    "InvalidArgument", "InvalidRequest", "MissingSecurityHeader", "NoSuchKey",
    "NoSuchBucket", "InvalidBucketName", "InvalidObjectName", "InternalError",
    "ServiceUnavailable", "SlowDown", "PermanentRedirect", "TemporaryRedirect",
}


class ProbeError(Exception):
    """Only fixed, credential-free messages may be passed to this exception."""


@dataclass(frozen=True, repr=False)
class Configuration:
    account_id: str = field(repr=False)
    bucket: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    max_upload_bytes: int

    @property
    def endpoint(self):
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    def object_url(self, key):
        return f"{self.endpoint}/{quote(self.bucket, safe='')}/{quote(key, safe='/')}"


@dataclass(frozen=True)
class AnonymousResult:
    status: int
    error_code: str = "none"
    body_kind: str = "empty"
    authentication_rejected: bool = False
    payload_returned: bool = False


def configuration(values):
    """Read the same six names as settings.py, with stricter probe safeguards."""
    missing = [name for name in VARIABLES if not values.get(name, "").strip()]
    if missing:
        raise ProbeError("Missing settings: " + ", ".join(missing))
    values = {name: values[name].strip() for name in VARIABLES}
    flag = values["MINUTES_R2_UPLOADS_ENABLED"].lower()
    if flag not in {"0", "false", "no", "off"}:
        raise ProbeError("MINUTES_R2_UPLOADS_ENABLED must explicitly be 0/false/no/off.")
    try:
        limit = int(values["MINUTES_MAX_UPLOAD_BYTES"])
    except ValueError:
        raise ProbeError("MINUTES_MAX_UPLOAD_BYTES must be a positive integer.") from None
    if limit <= 0:
        raise ProbeError("MINUTES_MAX_UPLOAD_BYTES must be a positive integer.")
    account = values["MINUTES_R2_ACCOUNT_ID"]
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
        raise ProbeError("MINUTES_R2_ACCOUNT_ID must be the 32-character Cloudflare account ID.")
    if values["MINUTES_R2_BUCKET_NAME"] != EXPECTED_BUCKET:
        raise ProbeError("This probe is restricted to bucket tambul-portal-media.")
    return Configuration(
        account, EXPECTED_BUCKET, values["MINUTES_R2_ACCESS_KEY_ID"],
        values["MINUTES_R2_SECRET_ACCESS_KEY"], limit,
    )


def prompt_configuration():
    """Keep credentials in this process, without modifying environment or files."""
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ProbeError("Hidden prompts require an interactive terminal; do not pipe credentials.")
    # --prompt deliberately ignores ambient environment variables, including AWS_*.
    values = {}
    for name, default in (
        ("MINUTES_R2_UPLOADS_ENABLED", "0"),
        ("MINUTES_MAX_UPLOAD_BYTES", "10485760"),
        ("MINUTES_R2_BUCKET_NAME", EXPECTED_BUCKET),
    ):
        values[name] = input(f"{name} [{default}]: ").strip() or default
    # getpass's fallback can echo input. Turn that warning into an error instead.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        for name in (
            "MINUTES_R2_ACCOUNT_ID", "MINUTES_R2_ACCESS_KEY_ID", "MINUTES_R2_SECRET_ACCESS_KEY",
        ):
            values[name] = getpass.getpass(f"{name} (hidden): ")
    return configuration(values)


def create_client(config):
    # No Django, django-storages, credential discovery, or database initialization.
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=config.endpoint, region_name="auto",
        aws_access_key_id=config.access_key, aws_secret_access_key=config.secret_key,
        config=Config(
            signature_version="s3v4", connect_timeout=5, read_timeout=15,
            # Make the SDK and anonymous control use the exact same URL shape.
            s3={"addressing_style": "path"},
            # Keep this probe to a single request attempt. An earlier timed-out
            # conditional PUT could otherwise commit after retry/cleanup.
            retries={"total_max_attempts": 1},
        ),
    )


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def inspect_anonymous_response(status, body, expected_payload):
    """Retain only bounded, allowlisted diagnostics; never retain response text."""
    payload_returned = bool(expected_payload and expected_payload in body)
    kind = "other" if body else "empty"
    if len(body) > MAX_ANONYMOUS_BODY_BYTES:
        return AnonymousResult(status, body_kind="oversized", payload_returned=payload_returned)
    # R2 sends UTF-8 XML. Reject DTD/entity/UTF-16 input before parsing untrusted
    # error documents; never print Message, Resource, ArgumentValue or headers.
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper() or b"\x00" in body:
        return AnonymousResult(status, body_kind=kind, payload_returned=payload_returned)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return AnonymousResult(status, body_kind=kind, payload_returned=payload_returned)
    if root.tag.rsplit("}", 1)[-1] != "Error":
        return AnonymousResult(status, body_kind=kind, payload_returned=payload_returned)
    fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in root}
    code = fields.get("Code", "")
    safe_code = code if code in SAFE_ERROR_CODES else "unrecognized"
    argument = fields.get("ArgumentName", "").lower()
    message = " ".join(fields.get("Message", "").lower().split())
    missing_authorization = argument == "authorization" or message in {
        "authorization", "missing authorization header", "authorization header is missing",
        "the authorization header is missing", "authorization header required",
        "missing authentication token",
    }
    auth_rejected = code in AUTHENTICATION_ERROR_CODES or (
        code in {"InvalidArgument", "InvalidRequest", "MissingSecurityHeader"}
        and missing_authorization
    )
    return AnonymousResult(status, safe_code, "s3-error", auth_rejected, payload_returned)


def anonymous_status(url, expected_payload):
    # This opener has no cookies, authentication handlers or signed query string.
    # Redirects and TLS verification failures must not become privacy successes.
    opener = build_opener(NoRedirects())
    request = Request(url, method="GET", headers={"Cache-Control": "no-cache"})
    try:
        try:
            response = opener.open(request, timeout=15)
        except HTTPError as error:
            # HTTPError also carries the response body; inspect it without ever
            # echoing its URL, reason string, headers, or raw XML/HTML content.
            response = error
        try:
            status = response.code
            try:
                body = response.read(MAX_ANONYMOUS_BODY_BYTES + 1)
            except (URLError, OSError):
                raise ProbeError(f"Anonymous S3 GET returned HTTP {status}; response reading failed.") from None
            return inspect_anonymous_response(status, body, expected_payload)
        finally:
            response.close()
    except (URLError, OSError):
        raise ProbeError("Anonymous S3 GET has no HTTP status: network/TLS request failed; details suppressed.") from None


def is_missing(error):
    response = getattr(error, "response", {})
    return (
        response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404
        and response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}
    )


def cleanup(client, config, key, metadata):
    """Delete this run's object only, then confirm absence with an authenticated HEAD."""
    try:
        result = client.head_object(Bucket=config.bucket, Key=key)
    except Exception as error:
        if is_missing(error):
            return
        raise
    if result.get("Metadata") != metadata:
        raise ProbeError("Cleanup refused: the temporary object does not carry this run's ownership marker.")
    client.delete_object(Bucket=config.bucket, Key=key)
    try:
        client.head_object(Bucket=config.bucket, Key=key)
    except Exception as error:
        if is_missing(error):
            return
        raise
    raise ProbeError("Cleanup could not confirm that the temporary object was removed.")


def run_probe(client, config, *, emit=print, check_anonymous=anonymous_status):
    run_id = uuid.uuid4().hex
    key = f"{PREFIX}{run_id}.txt"
    data = ("Tambul minutes R2 connection test: " + run_id + "\n").encode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    metadata = {"tambul-probe-id": run_id, "sha256": digest}
    if len(data) > config.max_upload_bytes:
        raise ProbeError("The synthetic test object exceeds MINUTES_MAX_UPLOAD_BYTES.")
    emit("Temporary object: " + key)
    phase = "upload"
    upload_confirmed = False
    try:
        # Even a UUID collision cannot overwrite an existing object. No ACL is sent.
        client.put_object(
            Bucket=config.bucket, Key=key, Body=data, IfNoneMatch="*",
            ContentType="text/plain", CacheControl="private, no-store", Metadata=metadata,
        )
        upload_confirmed = True
        emit("PASS: conditional temporary upload.")
        phase = "metadata verification"
        head = client.head_object(Bucket=config.bucket, Key=key)
        if head.get("ContentLength") != len(data) or head.get("Metadata") != metadata:
            raise ProbeError("Temporary object metadata/size verification failed.")
        if head.get("CacheControl") != "private, no-store":
            raise ProbeError("Temporary object private cache header verification failed.")
        phase = "authenticated retrieval"
        response = client.get_object(Bucket=config.bucket, Key=key)
        body = response["Body"]
        try:
            received = body.read(len(data) + 1)
        finally:
            body.close()
        if received != data or hashlib.sha256(received).hexdigest() != digest:
            raise ProbeError("Retrieved temporary bytes/SHA-256 did not match.")
        emit("PASS: authenticated retrieval, size and SHA-256 match.")
        phase = "anonymous S3 access check"
        emit("Anonymous target: HTTPS account S3 endpoint, same temporary key; unsigned; redirects disabled.")
        result = check_anonymous(config.object_url(key), data)
        emit(
            f"Anonymous S3 GET: HTTP {result.status}; S3 code={result.error_code}; "
            f"response={result.body_kind}; test_payload_returned={'yes' if result.payload_returned else 'no'}."
        )
        # Never let an error-looking XML body turn HTTP 200 into a passing test.
        if 200 <= result.status < 300 or result.payload_returned:
            raise ProbeError(f"SECURITY CHECK FAILED: anonymous object retrieval succeeded or returned test bytes (HTTP {result.status}).")
        if result.status not in {401, 403} and not (
            result.status == 400 and result.authentication_rejected
        ):
            raise ProbeError(
                f"Anonymous S3 GET is inconclusive (HTTP {result.status}; S3 code={result.error_code}); "
                "an unexpected response is not proof of access denial."
            )
        # Positive control: the same owned object must still exist before any
        # deletion, including when the anonymous endpoint returns HTTP 400.
        phase = "post-anonymous authenticated verification"
        head = client.head_object(Bucket=config.bucket, Key=key)
        if head.get("Metadata") != metadata or head.get("ContentLength") != len(data):
            raise ProbeError("The authenticated object changed during the anonymous access check.")
        emit("PASS: authenticated object still present after anonymous check, before deletion.")
        emit(f"PASS: unsigned S3 GET rejected (HTTP {result.status}; S3 code={result.error_code}).")
    except ProbeError:
        raise
    except Exception:
        # SDK exceptions may include endpoints, headers or server-provided text.
        raise ProbeError("R2 probe failed during " + phase + "; details suppressed to protect credentials.") from None
    finally:
        # Also attempt cleanup after an ambiguous PUT timeout or Ctrl+C. Inspect
        # ownership first: a rejected conditional PUT never permits blind deletion.
        try:
            cleanup(client, config, key, metadata)
        except Exception:
            emit("CLEANUP NOT VERIFIED: inspect only this exact temporary key: " + key)
            raise ProbeError("Temporary-object cleanup was not verified; do not proceed to migration.") from None
        if upload_confirmed:
            emit("PASS: temporary object absent after cleanup.")
        else:
            # A negative HEAD is not final when the original PUT's outcome is
            # unknown: it can still commit later. Never call that verified.
            emit("CLEANUP NOT VERIFIED: upload outcome uncertain; recheck this exact temporary key later: " + key)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", action="store_true", help="Enter all six values interactively; credentials hidden.")
    parser.add_argument(
        "--private-settings-checked", action="store_true",
        help="Confirm you inspected Cloudflare: r2.dev disabled, no public custom domain or public Worker route.",
    )
    args = parser.parse_args(argv)
    # A standalone process can suppress all SDK logging without affecting the app.
    logging.disable(logging.CRITICAL)
    try:
        if not args.private_settings_checked:
            raise ProbeError("Inspect Cloudflare bucket privacy first, then pass --private-settings-checked.")
        config = prompt_configuration() if args.prompt else configuration(os.environ)
        print("PASS: all six MINUTES_ settings validated; upload switch is OFF in this probe's configuration.")
        print("Bucket privacy settings: operator confirmed; public configuration is never changed by this probe.")
        client = create_client(config)
        try:
            run_probe(client, config)
        finally:
            client.close()
    except ProbeError as error:
        print("FAIL: " + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("STOPPED: check the temporary-object cleanup result above, if an upload started.", file=sys.stderr)
        return 130
    except Exception:
        print("FAIL: unable to initialize or complete the probe; exception details suppressed.", file=sys.stderr)
        return 1
    print("PASS: R2 connection test complete. No Django/database operation was performed.")
    print("Bucket privacy depends on the dashboard/Worker checks as well as the unsigned S3 denial.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
