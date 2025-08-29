"""Command-line interface for P³."""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.progress import track

from .database import P3Database
from .downloader import PodcastDownloader
from .transcriber import AudioTranscriber
from .cleaner import TranscriptCleaner
from .exporter import DigestExporter
from .writer import BlogWriter

console = Console()


def load_config(config_path: str = "config/feeds.yaml"):
    """Load configuration from YAML file."""
    config_file = Path(config_path)
    
    if not config_file.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        console.print("Copy config/feeds.yaml.example to config/feeds.yaml and configure your feeds")
        sys.exit(1)
    
    try:
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        sys.exit(1)


@click.group()
@click.option('--config', default="config/feeds.yaml", help='Configuration file path')
@click.option('--db', default="data/p3.duckdb", help='Database file path')
@click.pass_context
def main(ctx, config, db):
    """Parakeet Podcast Processor (P³) - Automated podcast processing."""
    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config
    ctx.obj['db_path'] = db
    ctx.obj['db'] = P3Database(db)


@main.command()
@click.option('--max-episodes', default=None, type=int, help='Max episodes per feed')
@click.pass_context
def fetch(ctx, max_episodes):
    """Download new podcast episodes from configured RSS feeds."""
    config = load_config(ctx.obj['config_path'])
    db = ctx.obj['db']
    
    settings = config.get('settings', {})
    max_eps = max_episodes or settings.get('max_episodes_per_feed', 10)
    
    downloader = PodcastDownloader(
        db=db,
        max_episodes=max_eps,
        audio_format=settings.get('audio_format', 'wav')
    )
    
    console.print("[blue]Fetching podcast episodes...[/blue]")
    
    feeds = config.get('feeds', [])
    if not feeds:
        console.print("[yellow]No feeds configured[/yellow]")
        return
    
    total_downloaded = 0
    results = downloader.fetch_all_feeds(feeds)
    
    # Display results table
    table = Table(title="Download Results")
    table.add_column("Podcast", style="cyan")
    table.add_column("New Episodes", style="green", justify="right")
    
    for name, count in results.items():
        table.add_row(name, str(count))
        total_downloaded += count
    
    console.print(table)
    console.print(f"[green]Total downloaded: {total_downloaded} episodes[/green]")


@main.command()
@click.option('--model', default=None, help='Whisper model to use')
@click.option('--episode-id', type=int, help='Transcribe specific episode')
@click.option('--skip-errors', is_flag=True, help='Skip episodes with previous errors')
@click.option('--max-retries', default=3, help='Maximum retry attempts before skipping')
@click.pass_context
def transcribe(ctx, model, episode_id, skip_errors, max_retries):
    """Transcribe downloaded audio files."""
    config = load_config(ctx.obj['config_path'])
    db = ctx.obj['db']
    
    settings = config.get('settings', {})
    whisper_model = model or settings.get('whisper_model', 'base')
    use_parakeet = settings.get('parakeet_enabled', False)
    
    transcriber = AudioTranscriber(
        db=db,
        whisper_model=whisper_model,
        use_parakeet=use_parakeet,
        parakeet_model=settings.get('parakeet_model', 'mlx-community/parakeet-tdt-0.6b-v2')
    )
    
    if episode_id:
        console.print(f"[blue]Transcribing episode {episode_id}...[/blue]")
        success = transcriber.transcribe_episode(episode_id, skip_errors=skip_errors)
        if success:
            console.print(f"[green]✓ Episode {episode_id} transcribed[/green]")
        else:
            console.print(f"[red]✗ Failed to transcribe episode {episode_id}[/red]")
            console.print("[dim]Use 'p3 errors --show-all' to see error details[/dim]")
    else:
        console.print("[blue]Transcribing all pending episodes...[/blue]")
        if skip_errors:
            console.print("[yellow]Skipping episodes with previous errors[/yellow]")
        
        transcribed = transcriber.transcribe_all_pending(
            skip_errors=skip_errors,
            max_retries=max_retries
        )
        
        console.print(f"[green]Transcribed {transcribed} episodes[/green]")


@main.command()
@click.option('--provider', default=None, help='LLM provider (openai, anthropic, ollama)')
@click.option('--model', default=None, help='LLM model to use')
@click.option('--episode-id', type=int, help='Process specific episode')
@click.option('--skip-errors', is_flag=True, help='Skip episodes with previous errors')
@click.option('--max-retries', default=3, help='Maximum retry attempts before skipping')
@click.pass_context
def digest(ctx, provider, model, episode_id, skip_errors, max_retries):
    """Generate structured summaries from transcripts."""
    config = load_config(ctx.obj['config_path'])
    db = ctx.obj['db']
    
    settings = config.get('settings', {})
    llm_provider = provider or settings.get('llm_provider', 'openai')
    llm_model = model or settings.get('llm_model', 'gpt-3.5-turbo')
    
    cleaner = TranscriptCleaner(
        db=db,
        llm_provider=llm_provider,
        llm_model=llm_model,
        ollama_base_url=settings.get('ollama_base_url', 'http://localhost:11434')
    )
    
    if episode_id:
        console.print(f"[blue]Processing episode {episode_id}...[/blue]")
        result = cleaner.generate_summary(episode_id, skip_errors=skip_errors)
        if result:
            console.print(f"[green]✓ Episode {episode_id} processed[/green]")
        else:
            console.print(f"[red]✗ Failed to process episode {episode_id}[/red]")
            console.print("[dim]Use 'p3 errors --show-all' to see error details[/dim]")
    else:
        console.print("[blue]Processing all transcribed episodes...[/blue]")
        if skip_errors:
            console.print("[yellow]Skipping episodes with previous errors[/yellow]")
        
        # Get transcribed episodes
        episodes = db.get_episodes_by_status('transcribed')
        if not episodes:
            console.print("[yellow]No episodes to process[/yellow]")
            return
        
        processed = 0
        failed = 0
        for episode in track(episodes, description="Processing..."):
            # Skip episodes that have exceeded max retries
            if episode.get('error_count', 0) >= max_retries:
                console.print(f"[dim]Skipping {episode['title'][:40]} - exceeded max retries[/dim]")
                continue
                
            result = cleaner.generate_summary(episode['id'], skip_errors=skip_errors)
            if result:
                processed += 1
            else:
                failed += 1
        
        console.print(f"[green]Processed {processed} episodes[/green]")
        if failed > 0:
            console.print(f"[yellow]⚠ {failed} episodes failed processing[/yellow]")
            console.print("[dim]Use 'p3 errors' to see details or 'p3 retry' to retry failed episodes[/dim]")


@main.command()
@click.option('--date', help='Export date (YYYY-MM-DD)')
@click.option('--format', multiple=True, help='Export format (markdown, json)')
@click.option('--output', help='Output file path')
@click.pass_context
def export(ctx, date, format, output):
    """Export daily digest summaries."""
    config = load_config(ctx.obj['config_path'])
    db = ctx.obj['db']
    
    # Parse date
    if date:
        try:
            target_date = datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            console.print("[red]Invalid date format. Use YYYY-MM-DD[/red]")
            return
    else:
        target_date = datetime.now()
    
    # Get export formats
    formats = list(format) if format else config.get('settings', {}).get('export_format', ['markdown'])
    
    exporter = DigestExporter(db)
    
    summaries = db.get_summaries_by_date(target_date)
    
    if not summaries:
        console.print(f"[yellow]No summaries found for {target_date.date()}[/yellow]")
        return
    
    console.print(f"[blue]Exporting {len(summaries)} summaries for {target_date.date()}[/blue]")
    
    for fmt in formats:
        if fmt == 'markdown':
            content = exporter.export_markdown(summaries, target_date.date())
            filename = output or f"digest_{target_date.strftime('%Y-%m-%d')}.md"
        elif fmt == 'json':
            content = exporter.export_json(summaries, target_date.date())
            filename = output or f"digest_{target_date.strftime('%Y-%m-%d')}.json"
        else:
            console.print(f"[red]Unsupported format: {fmt}[/red]")
            continue
        
        # Write to file
        with open(filename, 'w') as f:
            f.write(content)
        
        console.print(f"[green]✓ Exported {fmt}: {filename}[/green]")


@main.command()
@click.pass_context
def status(ctx):
    """Show processing status of episodes."""
    db = ctx.obj['db']
    
    # Count episodes by status
    statuses = ['downloaded', 'transcribed', 'processed', 'failed']
    counts = {}
    
    for status in statuses:
        episodes = db.get_episodes_by_status(status)
        counts[status] = len(episodes)
    
    # Also get error episodes
    error_episodes = db.get_error_episodes()
    error_count = len(error_episodes)
    
    table = Table(title="Episode Processing Status")
    table.add_column("Status", style="cyan")
    table.add_column("Count", style="green", justify="right")
    
    for status, count in counts.items():
        style = "red" if status == "failed" else "green"
        table.add_row(status.title(), str(count), style=style)
    
    console.print(table)
    
    # Show error summary if there are errors
    if error_count > 0:
        console.print(f"\n[yellow]⚠ {error_count} episodes have errors[/yellow]")
        console.print("[dim]Use 'p3 errors' to see details[/dim]")


@main.command()
@click.option('--episode-id', type=int, help='Mark specific episode as processed')
@click.option('--podcast', help='Mark all episodes from podcast as processed')
@click.option('--reason', help='Reason for marking as processed')
@click.pass_context
def mark_processed(ctx, episode_id, podcast, reason):
    """Mark episodes as processed (useful for error recovery)."""
    db = ctx.obj['db']
    
    if episode_id:
        # Mark specific episode
        episode = db.get_episode_by_id(episode_id)
        if not episode:
            console.print(f"[red]Episode {episode_id} not found[/red]")
            return
        
        db.mark_episode_as_processed(episode_id, reason)
        console.print(f"[green]✓ Marked episode {episode_id} ({episode['title']}) as processed[/green]")
        if reason:
            console.print(f"  Reason: {reason}")
    
    elif podcast:
        # Mark all episodes from podcast
        # First get all episodes with errors or in failed state
        error_episodes = db.get_error_episodes()
        podcast_episodes = [e for e in error_episodes if podcast.lower() in e['podcast_title'].lower()]
        
        if not podcast_episodes:
            console.print(f"[yellow]No error episodes found for podcast matching '{podcast}'[/yellow]")
            return
        
        console.print(f"[yellow]Found {len(podcast_episodes)} error episodes for '{podcast}'[/yellow]")
        for ep in podcast_episodes:
            db.mark_episode_as_processed(ep['id'], reason)
            console.print(f"  ✓ {ep['title']}")
        
        console.print(f"[green]Marked {len(podcast_episodes)} episodes as processed[/green]")
    
    else:
        console.print("[red]Please specify --episode-id or --podcast[/red]")


@main.command()
@click.option('--episode-id', type=int, help='Retry specific episode')
@click.option('--all', is_flag=True, help='Retry all failed episodes')
@click.option('--reset-errors', is_flag=True, help='Reset error count')
@click.pass_context
def retry(ctx, episode_id, all, reset_errors):
    """Retry failed episodes."""
    db = ctx.obj['db']
    
    if episode_id:
        # Retry specific episode
        episode = db.get_episode_by_id(episode_id)
        if not episode:
            console.print(f"[red]Episode {episode_id} not found[/red]")
            return
        
        if reset_errors:
            db.reset_episode_errors(episode_id)
            console.print(f"[green]✓ Reset errors for episode {episode_id}[/green]")
        
        db.update_episode_status(episode_id, 'downloaded')
        console.print(f"[green]✓ Episode {episode_id} ready for retry[/green]")
    
    elif all:
        # Retry all failed episodes
        error_episodes = db.get_error_episodes()
        
        if not error_episodes:
            console.print("[yellow]No failed episodes found[/yellow]")
            return
        
        console.print(f"[yellow]Found {len(error_episodes)} failed episodes[/yellow]")
        for ep in error_episodes:
            if reset_errors:
                db.reset_episode_errors(ep['id'])
            db.update_episode_status(ep['id'], 'downloaded')
            console.print(f"  ✓ Reset: {ep['title']}")
        
        console.print(f"[green]Reset {len(error_episodes)} episodes for retry[/green]")
    
    else:
        console.print("[red]Please specify --episode-id or --all[/red]")


@main.command()
@click.option('--show-all', is_flag=True, help='Show all details')
@click.pass_context
def errors(ctx, show_all):
    """List episodes with errors."""
    db = ctx.obj['db']
    
    error_episodes = db.get_error_episodes()
    
    if not error_episodes:
        console.print("[green]No episodes with errors found[/green]")
        return
    
    table = Table(title=f"Episodes with Errors ({len(error_episodes)} total)")
    table.add_column("ID", style="cyan")
    table.add_column("Podcast", style="magenta")
    table.add_column("Episode", style="white")
    table.add_column("Status", style="red")
    table.add_column("Errors", style="yellow", justify="right")
    
    if show_all:
        table.add_column("Last Error", style="dim")
        table.add_column("Timestamp", style="dim")
    
    for ep in error_episodes:
        row = [
            str(ep['id']),
            ep['podcast_title'][:30],
            ep['title'][:40],
            ep['status'],
            str(ep['error_count'])
        ]
        
        if show_all:
            error_msg = ep['last_error'] or "N/A"
            if len(error_msg) > 50:
                error_msg = error_msg[:47] + "..."
            row.append(error_msg)
            row.append(str(ep['error_timestamp'] or "N/A")[:19])
        
        table.add_row(*row)
    
    console.print(table)
    
    if not show_all:
        console.print("\n[dim]Use --show-all to see error details[/dim]")


@main.command()
@click.option('--topic', required=True, help='Blog post topic/angle')
@click.option('--date', help='Date to use for digest (YYYY-MM-DD), defaults to today')
@click.option('--target-grade', default=91.0, help='Target grade for AP English teacher (default: 91.0)')
@click.pass_context
def write(ctx, topic, date, target_grade):
    """Generate blog post from podcast digest using AP English grading system.
    
    Inspired by Tomasz Tunguz's innovative iterative writing approach.
    """
    config = load_config(ctx.obj['config_path'])
    db = ctx.obj['db']
    
    settings = config.get('settings', {})
    llm_provider = settings.get('llm_provider', 'ollama')
    llm_model = settings.get('llm_model', 'llama3.2:latest')
    
    # Parse date
    if date:
        try:
            target_date = datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            console.print("[red]Invalid date format. Use YYYY-MM-DD[/red]")
            return
    else:
        target_date = datetime.now()
    
    # Get summaries for the date
    summaries = db.get_summaries_by_date(target_date)
    
    if not summaries:
        console.print(f"[yellow]No summaries found for {target_date.date()}[/yellow]")
        console.print("Run 'p3 digest' first to generate summaries")
        return
    
    writer = BlogWriter(
        db=db,
        llm_provider=llm_provider,
        llm_model=llm_model,
        target_grade=target_grade
    )
    
    console.print(f"[blue]Generating blog post: '{topic}'[/blue]")
    console.print(f"Using {len(summaries)} podcast summaries from {target_date.date()}")
    console.print(f"Target grade: {target_grade}/100 (inspired by Tomasz Tunguz)")
    
    # Use the first summary as primary source (could be enhanced to combine multiple)
    primary_summary = summaries[0]
    
    with console.status("[bold green]Writing and grading blog post..."):
        blog_result = writer.generate_blog_post_from_digest(topic, primary_summary)
    
    # Show results
    console.print(f"\n[green]✓ Blog post generated![/green]")
    console.print(f"Final Grade: {blog_result['final_grade']} ({blog_result['final_score']}/100)")
    console.print(f"Iterations: {len(blog_result['iterations'])}")
    
    # Save blog post
    file_path = writer.save_blog_post(blog_result)
    console.print(f"Saved to: {file_path}")
    
    # Generate social media posts
    console.print(f"\n[blue]Generating social media posts...[/blue]")
    social_posts = writer.generate_social_posts(blog_result)
    
    # Display social posts
    console.print("\n[cyan]📱 Twitter Posts:[/cyan]")
    for i, post in enumerate(social_posts['twitter'], 1):
        console.print(f"{i}. {post}")
    
    console.print("\n[cyan]💼 LinkedIn Posts:[/cyan]")
    for i, post in enumerate(social_posts['linkedin'], 1):
        console.print(f"{i}. {post[:100]}...")
    
    # Show final blog post preview
    console.print(f"\n[cyan]📄 Blog Post Preview:[/cyan]")
    console.print("-" * 50)
    preview = blog_result['final_post'][:500]
    console.print(f"{preview}...")
    console.print("-" * 50)
    console.print(f"[green]Complete post saved to: {file_path}[/green]")


@main.command()
@click.pass_context  
def init(ctx):
    """Initialize P³ configuration and directories."""
    console.print("[blue]Initializing P³...[/blue]")
    
    # Create directories
    dirs = ['data', 'config', 'logs', 'data/audio', 'exports', 'blog_posts']
    for dir_name in dirs:
        Path(dir_name).mkdir(parents=True, exist_ok=True)
        console.print(f"✓ Created directory: {dir_name}")
    
    # Copy example config if it doesn't exist
    config_path = Path("config/feeds.yaml")
    example_path = Path("config/feeds.yaml.example")
    
    if not config_path.exists() and example_path.exists():
        import shutil
        shutil.copy(example_path, config_path)
        console.print("✓ Created config/feeds.yaml from example")
    
    # Initialize database
    db = P3Database("data/p3.duckdb")
    db.close()
    console.print("✓ Initialized database")
    
    console.print("[green]P³ initialized successfully![/green]")
    console.print("Next steps:")
    console.print("1. Edit config/feeds.yaml with your RSS feeds")
    console.print("2. Run 'p3 fetch' to download episodes")
    console.print("3. Run 'p3 transcribe' to transcribe audio")
    console.print("4. Run 'p3 digest' to generate summaries")
    console.print("5. Run 'p3 write --topic \"Your Topic\"' to generate blog posts")


if __name__ == '__main__':
    main()
