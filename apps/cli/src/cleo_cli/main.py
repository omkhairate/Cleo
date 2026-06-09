import json

import httpx
import typer

from assistant_core.config import get_settings


app = typer.Typer(help="Cleo terminal client.")
DEFAULT_CONVERSATION_ID = "terminal"


@app.command()
def chat(
    message: str,
    conversation_id: str = typer.Option(DEFAULT_CONVERSATION_ID, "--conversation"),
    stream: bool = typer.Option(True, "--stream/--no-stream"),
) -> None:
    """Send a message to the Cleo API."""
    settings = get_settings()
    endpoint = "/chat/stream" if stream else "/chat"
    payload = {"message": message, "conversation_id": conversation_id}
    try:
        if stream:
            timeout = httpx.Timeout(
                connect=10.0,
                read=None,
                write=settings.api_timeout_seconds,
                pool=10.0,
            )
            with httpx.stream(
                "POST",
                f"{settings.api_url}{endpoint}",
                json=payload,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_text():
                    if chunk:
                        typer.echo(chunk, nl=False)
                typer.echo()
            return

        response = httpx.post(
            f"{settings.api_url}{endpoint}",
            json=payload,
            timeout=settings.api_timeout_seconds,
        )
        response.raise_for_status()
    except httpx.ReadTimeout:
        typer.secho(
            (
                "Cleo timed out waiting for the API response. "
                f"Try again, or increase CLEO_API_TIMEOUT_SECONDS above {settings.api_timeout_seconds}."
            ),
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    except httpx.HTTPError as exc:
        typer.secho(f"API request failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    payload = response.json()
    typer.echo(payload["reply"])


@app.command("command")
def command(
    instruction: str,
    conversation_id: str = typer.Option("command", "--conversation"),
) -> None:
    """Run a multi-agent command workflow through Cleo."""
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.api_url}/command",
            json={"command": instruction, "conversation_id": conversation_id},
            timeout=settings.api_timeout_seconds,
        )
        response.raise_for_status()
    except httpx.ReadTimeout:
        typer.secho(
            (
                "Cleo timed out while running the command workflow. "
                f"Try again, or increase CLEO_API_TIMEOUT_SECONDS above {settings.api_timeout_seconds}."
            ),
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    except httpx.HTTPError as exc:
        typer.secho(f"API request failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    payload = response.json()
    typer.echo(f"Summary: {payload['summary']}")
    typer.echo("")
    typer.echo("Tasks:")
    for task in payload["tasks"]:
        typer.echo(
            f"- {task['task_id']} [{task['specialist']}] {task['title']} -> {task['status']}"
        )
    typer.echo("")
    typer.echo("Final response:")
    typer.echo(payload["final_response"])


@app.command("connectors")
def connectors() -> None:
    """List known connectors."""
    settings = get_settings()
    response = httpx.get(f"{settings.api_url}/connectors", timeout=settings.api_timeout_seconds)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), indent=2))


@app.command("brain-graph")
def brain_graph() -> None:
    """Show the assistant brain graph payload."""
    settings = get_settings()
    response = httpx.get(f"{settings.api_url}/brain-graph", timeout=settings.api_timeout_seconds)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), indent=2))


@app.command("model-status")
def model_status() -> None:
    """Check the local model runtime status."""
    settings = get_settings()
    response = httpx.get(f"{settings.api_url}/model-status", timeout=settings.api_timeout_seconds)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), indent=2))


@app.command("history")
def history(
    conversation_id: str = typer.Option(DEFAULT_CONVERSATION_ID, "--conversation"),
) -> None:
    """Show stored messages for one conversation."""
    settings = get_settings()
    response = httpx.get(
        f"{settings.api_url}/conversations/{conversation_id}",
        timeout=settings.api_timeout_seconds,
    )
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), indent=2))


@app.command("clear-history")
def clear_history(
    conversation_id: str = typer.Option(DEFAULT_CONVERSATION_ID, "--conversation"),
) -> None:
    """Clear stored messages for one conversation."""
    settings = get_settings()
    response = httpx.delete(
        f"{settings.api_url}/conversations/{conversation_id}",
        timeout=settings.api_timeout_seconds,
    )
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), indent=2))


@app.command("profile")
def profile() -> None:
    """Show the learned user profile."""
    settings = get_settings()
    response = httpx.get(
        f"{settings.api_url}/profile",
        timeout=settings.api_timeout_seconds,
    )
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), indent=2))


@app.command("set-pref")
def set_pref(key: str, value: str) -> None:
    """Set one user preference manually."""
    settings = get_settings()
    response = httpx.patch(
        f"{settings.api_url}/profile",
        json={"preferences": {key: value}},
        timeout=settings.api_timeout_seconds,
    )
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), indent=2))


@app.command("import-chatgpt")
def import_chatgpt(file_path: str) -> None:
    """Import a ChatGPT export JSON into the current in-memory Cleo session."""
    settings = get_settings()
    response = httpx.post(
        f"{settings.api_url}/imports/chatgpt",
        json={"file_path": file_path},
        timeout=settings.api_timeout_seconds,
    )
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    app()
