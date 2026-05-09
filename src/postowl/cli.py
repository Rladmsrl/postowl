from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

import typer
from dateutil import parser as dateparser
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from postowl.config import DEFAULT_CONFIG_FILE, load_config, save_config

app = typer.Typer(name="postowl", help="PostOwl - Smart email agent with RAG")
accounts_app = typer.Typer(help="Manage email accounts")
app.add_typer(accounts_app, name="accounts")

console = Console()


def _get_services(config_path: Path | None = None):
    from postowl.agent.rag import RAGEngine
    from postowl.llm.client import LLMClient
    from postowl.memory.index import MemoryIndex
    from postowl.storage.database import Database
    from postowl.storage.vectorstore import VectorStore

    settings = load_config(config_path)
    db = Database(settings.db_path)
    llm = LLMClient(settings.llm)
    vs = VectorStore(settings.chroma_path, settings.embedding)
    memory_index = MemoryIndex(db, llm)
    rag = RAGEngine(llm, db, vs, memory_index=memory_index)
    return settings, db, llm, vs, rag


@app.command()
def init(config_path: Path | None = typer.Option(None, "--config", "-c", help="Config file path")):
    """Initialize PostOwl configuration."""
    path = config_path or DEFAULT_CONFIG_FILE
    if path.exists():
        if not typer.confirm(f"Config already exists at {path}. Overwrite?"):
            raise typer.Abort()

    from postowl.config import EmbeddingConfig, LLMConfig, SchedulerConfig, Settings, TelegramConfig

    base_url = typer.prompt("LLM API base URL", default="https://api.openai.com/v1")
    api_key = typer.prompt("LLM API key", hide_input=True)
    chat_model = typer.prompt("Chat model name", default="gpt-4o-mini")

    emb_base_url = typer.prompt("Embedding API base URL (same as LLM if same provider)", default=base_url)
    emb_api_key = typer.prompt("Embedding API key (same as LLM if same provider)", default=api_key, hide_input=True)
    emb_model = typer.prompt("Embedding model name", default="text-embedding-3-small")

    bot_token = typer.prompt("Telegram bot token (leave empty to skip)", default="")
    user_ids_str = typer.prompt("Allowed Telegram user IDs (comma-separated, empty=all)", default="")

    user_ids = [int(x.strip()) for x in user_ids_str.split(",") if x.strip()] if user_ids_str else []

    settings = Settings(
        llm=LLMConfig(base_url=base_url, api_key=api_key, chat_model=chat_model),
        embedding=EmbeddingConfig(base_url=emb_base_url, api_key=emb_api_key, model=emb_model),
        telegram=TelegramConfig(bot_token=bot_token, allowed_user_ids=user_ids),
        scheduler=SchedulerConfig(),
    )
    save_config(settings, path)
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"[green]Configuration saved to {path}[/green]")


@app.command()
def config(config_path: Path | None = typer.Option(None, "--config", "-c")):
    """Show current configuration."""
    settings = load_config(config_path)
    table = Table(title="PostOwl Configuration")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Config dir", str(settings.config_dir))
    table.add_row("Database", str(settings.db_path))
    table.add_row("ChromaDB", str(settings.chroma_path))
    table.add_row("LLM base URL", settings.llm.base_url)
    table.add_row("Chat model", settings.llm.chat_model)
    table.add_row("Embedding base URL", settings.embedding.base_url)
    table.add_row("Embedding model", settings.embedding.model)
    table.add_row("Telegram bot", "configured" if settings.telegram.bot_token else "not set")
    table.add_row("Fetch interval", f"{settings.scheduler.fetch_interval_minutes} min")
    console.print(table)


@accounts_app.command("add")
def accounts_add(config_path: Path | None = typer.Option(None, "--config", "-c")):
    """Add an email account."""
    from postowl.email.client import store_password
    from postowl.models import EmailAccount
    from postowl.storage.database import Database

    settings = load_config(config_path)
    name = typer.prompt("Account name (e.g., 'work')")
    email_addr = typer.prompt("Email address")
    imap_server = typer.prompt("IMAP server (e.g., imap.gmail.com)")
    imap_port = typer.prompt("IMAP port", default=993, type=int)
    username = typer.prompt("Username (usually same as email)", default=email_addr)
    password = typer.prompt("Password / App password", hide_input=True)
    use_ssl = typer.confirm("Use SSL?", default=True)

    store_password(email_addr, password)
    db = Database(settings.db_path)
    try:
        account = EmailAccount(name=name, email=email_addr, imap_server=imap_server,
                               imap_port=imap_port, username=username, use_ssl=use_ssl)
        aid = db.add_account(account)
        console.print(f"[green]Account '{name}' added (ID: {aid}). Password stored in system keychain.[/green]")
    finally:
        db.close()


@accounts_app.command("list")
def accounts_list(config_path: Path | None = typer.Option(None, "--config", "-c")):
    """List email accounts."""
    settings = load_config(config_path)
    db = Database(settings.db_path)
    try:
        accounts = db.get_accounts()
        if not accounts:
            console.print("[yellow]No accounts configured. Use 'postowl accounts add' to add one.[/yellow]")
            return
        table = Table(title="Email Accounts")
        table.add_column("ID", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Email")
        table.add_column("Server")
        table.add_column("Last UID", style="dim")
        for a in accounts:
            table.add_row(str(a.id), a.name, a.email, a.imap_server, str(a.last_uid))
        console.print(table)
    finally:
        db.close()


@accounts_app.command("rm")
def accounts_remove(
    account_id: int = typer.Argument(help="Account ID to remove"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
):
    """Remove an email account."""
    from postowl.email.client import delete_password
    from postowl.storage.database import Database

    settings = load_config(config_path)
    db = Database(settings.db_path)
    try:
        account = db.get_account(account_id)
        if not account:
            console.print(f"[red]Account {account_id} not found.[/red]")
            raise typer.Exit(1)
        if typer.confirm(f"Delete account '{account.name}' ({account.email}) and all its emails?"):
            delete_password(account.email)
            db.delete_account(account_id)
            console.print(f"[green]Account '{account.name}' removed.[/green]")
    finally:
        db.close()


@app.command()
def fetch(
    limit: int | None = typer.Option(None, "--limit", "-l", help="Max emails to fetch per account"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
):
    """Fetch new emails from all accounts."""
    settings, db, llm, vs, rag = _get_services(config_path)
    from postowl.email.client import EmailClient
    from postowl.pipeline import fetch_and_process

    accounts = db.get_accounts()
    if not accounts:
        console.print("[yellow]No accounts configured.[/yellow]")
        return

    def _cli_progress(email, stage):
        console.print(f"  [dim]Processing: {email.subject}[/dim]")

    total_new = 0
    for account in accounts:
        console.print(f"[cyan]Fetching from {account.name} ({account.email})...[/cyan]")

        fetch_limit = limit
        if fetch_limit is None and account.last_uid == 0:
            try:
                with EmailClient(account) as client:
                    info = client._client.select_folder("INBOX", readonly=True)
                    total_count = info[b"EXISTS"]
                if total_count > 1000:
                    console.print(f"  [yellow]Inbox has {total_count} emails.[/yellow]")
                    fetch_limit = typer.prompt(
                        "  How many emails to fetch? (enter a number, or 'all')",
                        default="200",
                    )
                    fetch_limit = None if fetch_limit.lower() == "all" else int(fetch_limit)
            except Exception:
                pass

        new_emails = fetch_and_process(
            account, llm, db, vs,
            max_workers=settings.scheduler.max_workers,
            limit=fetch_limit,
            memory_index=rag.memory_index,
            on_progress=_cli_progress,
            on_error=lambda acc, e: console.print(f"  [red]Error: {e}[/red]"),
        )
        if not new_emails:
            console.print("  No new emails.")
        total_new += len(new_emails)

    console.print(f"\n[green]Done! {total_new} new emails processed.[/green]")
    db.close()


@app.command()
def summary(
    period: str = typer.Option("today", help="Period: today, week, or YYYY-MM-DD"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
):
    """Show email summary for a period."""
    settings, db, llm, vs, _ = _get_services(config_path)
    from postowl.agent.summarizer import summarize_emails

    now = datetime.now()
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        since = now - timedelta(days=7)
    else:
        since = dateparser.parse(period)

    emails = db.get_emails(since=since)
    if not emails:
        console.print("[yellow]No emails found for this period.[/yellow]")
        db.close()
        return

    console.print(f"[cyan]Summarizing {len(emails)} emails...[/cyan]")
    result = summarize_emails(llm, emails)
    console.print(Panel(result, title=f"Email Summary ({period})", border_style="green"))
    db.close()


@app.command()
def search(
    query: str = typer.Argument(help="Search query"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
):
    """Search emails by keyword."""
    settings = load_config(config_path)
    from postowl.storage.database import Database

    db = Database(settings.db_path)
    try:
        results = db.search_emails(query)
        if not results:
            console.print("[yellow]No emails found.[/yellow]")
            return
        table = Table(title=f"Search: '{query}'")
        table.add_column("ID", style="cyan", width=5)
        table.add_column("Date", width=12)
        table.add_column("From", width=25)
        table.add_column("Subject", width=40)
        table.add_column("Category", width=12)
        for e in results:
            date_str = e.date.strftime("%m-%d %H:%M") if e.date else ""
            table.add_row(str(e.id), date_str, e.sender_addr[:25], (e.subject or "")[:40],
                          e.category.value)
        console.print(table)
    finally:
        db.close()


@app.command()
def ask(
    question: str = typer.Argument(help="Question to ask about your emails"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
):
    """Ask a question about your emails using RAG."""
    _, db, _, _, rag = _get_services(config_path)
    console.print("[cyan]Thinking...[/cyan]")
    answer = rag.query(question)
    console.print(Panel(answer, title="PostOwl", border_style="green"))
    db.close()


@app.command()
def remind(
    time_str: str = typer.Argument(help="When to remind (e.g., '2024-01-15 09:00', 'tomorrow 10:00')"),
    message: str = typer.Argument(help="Reminder message"),
    email_id: int | None = typer.Option(None, "--email", "-e", help="Link to email ID"),
    config_path: Path | None = typer.Option(None, "--config", "-c"),
):
    """Add a reminder."""
    from postowl.models import Reminder

    settings = load_config(config_path)
    from postowl.storage.database import Database

    db = Database(settings.db_path)
    try:
        remind_at = dateparser.parse(time_str)
        if not remind_at:
            console.print("[red]Could not parse time.[/red]")
            raise typer.Exit(1)
        r = Reminder(email_id=email_id, remind_at=remind_at, message=message)
        rid = db.add_reminder(r)
        console.print(f"[green]Reminder #{rid} set for {remind_at.strftime('%Y-%m-%d %H:%M')}[/green]")
    finally:
        db.close()


@app.command()
def reminders(config_path: Path | None = typer.Option(None, "--config", "-c")):
    """Show pending reminders."""
    settings = load_config(config_path)
    from postowl.storage.database import Database

    db = Database(settings.db_path)
    try:
        items = db.get_all_reminders(include_sent=False)
        if not items:
            console.print("[yellow]No pending reminders.[/yellow]")
            return
        table = Table(title="Pending Reminders")
        table.add_column("ID", style="cyan", width=5)
        table.add_column("Time", width=18)
        table.add_column("Message")
        table.add_column("Email", width=8)
        for r in items:
            table.add_row(str(r.id), r.remind_at.strftime("%Y-%m-%d %H:%M"),
                          r.message, str(r.email_id or "-"))
        console.print(table)
    finally:
        db.close()


@app.command()
def serve(config_path: Path | None = typer.Option(None, "--config", "-c")):
    """Start Telegram bot and scheduler."""
    from postowl.app import run

    settings = load_config(config_path)
    if not settings.telegram.bot_token:
        console.print("[red]Telegram bot token not configured. Run 'postowl init' first.[/red]")
        raise typer.Exit(1)
    console.print("[green]Starting PostOwl...[/green]")
    run(settings)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    app()


if __name__ == "__main__":
    main()
