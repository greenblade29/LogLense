import re
import sys
import shutil
import hashlib
import typer
import json

from collections import defaultdict
from pathlib import Path
from collections import deque
from rich.status import Status
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.table import Table
from rich.panel import Panel
from .providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
)

app = typer.Typer(no_args_is_help=True, help="LogLense: An AI-powered log summarizer.")
cache_app = typer.Typer()
app.add_typer(cache_app, name="cache", help="Manage the local cache.")

console = Console()
# --- Caching Setup ---
CACHE_DIR = Path.home() / ".cache" / "loglense"
CONFIG_DIR = Path.home() / ".config" / "loglense"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Model configuration with categories
MODEL_CATEGORIES = {
    "OpenAI": {
        "gpt-4o": {"full_id": "openai/gpt-4o", "description": "GPT-4 Optimized - Latest multimodal model", "env_key": "OPENAI_API_KEY"},
        "gpt-4o-mini": {"full_id": "openai/gpt-4o-mini", "description": "Smaller, faster GPT-4 for simple tasks", "env_key": "OPENAI_API_KEY"},
    },
    "Anthropic": {
        "opus-4": {"full_id": "anthropic/claude-opus-4-0", "description": "Anthropic's state-of-the-art flagship model", "env_key": "ANTHROPIC_API_KEY"},
        "sonnet-4": {"full_id": "anthropic/claude-sonnet-4-0", "description": "Balanced model with superior performance", "env_key": "ANTHROPIC_API_KEY"},
        "sonnet-3.7": {"full_id": "anthropic/claude-3-7-sonnet", "description": "Advanced iteration of sonnet family", "env_key": "ANTHROPIC_API_KEY"},
    },
    "Google": {
        "gemini-flash": {"full_id": "gemini/gemini-2.0-flash", "description": "Fast Gemini model", "env_key": "GEMINI_API_KEY"},
        "gemini-pro": {"full_id": "gemini/gemini-2.5-pro", "description": "Advanced Gemini model", "env_key": "GEMINI_API_KEY"},
    },
    "Local Models": {
        "ollama-llama3": {"full_id": "ollama/llama3", "description": "Local Llama 3 via Ollama", "env_key": None},
        "ollama-gemma": {"full_id": "ollama/gemma", "description": "Local Gemma via Ollama", "env_key": None},
    },
    "Other": {
        "mistral-large": {"full_id": "mistral/mistral-large-latest", "description": "Mistral's flagship model", "env_key": "MISTRAL_API_KEY"},
        "deepseek-chat": {"full_id": "deepseek/deepseek-chat", "description": "DeepSeek chat model", "env_key": "DEEPSEEK_API_KEY"},
    }
}

def load_config():
    """Load configuration from file."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_config(config):
    """Save configuration to file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def set_env_from_config(config):
    """Set environment variables from config."""
    import os
    if "api_keys" in config:
        for key, value in config["api_keys"].items():
            if value:
                os.environ[key] = value

def parse_log_stream(stream):
    """
    Reads a log stream line-by-line, capturing first/last lines, errors, and a content hash.
    """
    console.print("🔬 Analyzing log stream...")
    
    total_lines = 0
    error_pattern = re.compile(r".*(ERROR|CRITICAL|FATAL|Exception).*", re.IGNORECASE)
    errors = defaultdict(int)
    hasher = hashlib.sha256()

    first_lines = []
    last_lines = deque(maxlen=20) # Keeps only the last 20 lines in memory

    for line_bytes in stream.buffer:
        hasher.update(line_bytes)
        try:
            line = line_bytes.decode('utf-8')
        except UnicodeDecodeError:
            continue

        if total_lines < 20:
            first_lines.append(line.strip())
        
        last_lines.append(line.strip())

        total_lines += 1
        match = error_pattern.match(line)
        if match:
            error_key = match.group(1).upper() + ": " + line.strip()[:120]
            errors[error_key] += 1

    cache_hash = hasher.hexdigest()
    log_summary = {
        "total_lines": total_lines, 
        "unique_errors": dict(errors),
        "first_lines": first_lines,
        "last_lines": list(last_lines)
    }
    
    return cache_hash, log_summary

@app.command(name="configure", help="Configure default model and API keys.")
def configure_command():
    """Interactive configuration for model selection and API keys."""
    console.print(Panel.fit("🔧 LogLense Configuration", style="bold cyan"))
    console.print()
    
    config = load_config()
    
    # Model selection
    console.print("[bold cyan]Select Default Model[/bold cyan]\n")
    
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("#", style="dim", width=4)
    table.add_column("Provider", style="cyan", width=12)
    table.add_column("Model", style="green", width=15)
    table.add_column("Description", style="white")
    
    options = []
    option_num = 1
    
    for category, models in MODEL_CATEGORIES.items():
        for model_alias, model_info in models.items():
            table.add_row(
                str(option_num),
                category,
                model_alias,
                model_info["description"]
            )
            options.append((model_alias, model_info))
            option_num += 1
    
    console.print(table)
    console.print()
    
    # Get current default if exists
    current_default = config.get("default_model", "2")
    current_idx = "2"  # Default to gpt-4o-mini
    
    # Find current model index
    for idx, (alias, info) in enumerate(options):
        if info["full_id"] == current_default:
            current_idx = str(idx + 1)
            break
    
    choice = Prompt.ask(
        "[bold]Select default model",
        choices=[str(i) for i in range(1, len(options) + 1)],
        default=current_idx
    )
    
    selected_alias, selected_info = options[int(choice) - 1]
    config["default_model"] = selected_info["full_id"]
    
    console.print(f"\n✅ Default model set to: [bold green]{selected_alias}[/bold green]")
    
    # API Key configuration
    required_key = selected_info.get("env_key")
    
    if required_key:
        console.print(f"\n[bold cyan]API Key Configuration[/bold cyan]")
        console.print(f"This model requires: [yellow]{required_key}[/yellow]\n")
        
        # Initialize api_keys dict if not exists
        if "api_keys" not in config:
            config["api_keys"] = {}
        
        # Check if key already exists
        current_key = config["api_keys"].get(required_key, "")
        if current_key:
            masked_key = current_key[:8] + "..." + current_key[-4:] if len(current_key) > 12 else "***"
            update = Prompt.ask(
                f"API key already set ({masked_key}). Update?",
                choices=["y", "n"],
                default="n"
            )
            if update.lower() == "y":
                api_key = Prompt.ask(f"Enter your {required_key}", password=True)
                if api_key:
                    config["api_keys"][required_key] = api_key
                    console.print(f"✅ API key saved for {required_key}")
        else:
            api_key = Prompt.ask(f"Enter your {required_key}", password=True)
            if api_key:
                config["api_keys"][required_key] = api_key
                console.print(f"✅ API key saved for {required_key}")
    else:
        console.print("\n[dim]This model doesn't require an API key.[/dim]")
    
    # Save configuration
    save_config(config)
    console.print("\n✅ Configuration saved successfully!")
    console.print(f"📁 Config location: [dim]{CONFIG_FILE}[/dim]")
    console.print("\n[dim]Tip: Run 'loglense configure' again to set up additional providers.[/dim]")

@app.command(name="analyze", help="Analyze a log stream piped from stdin.")
def analyze_command(
    model: str = typer.Option(None, "--model", "-m", help="Model to use (e.g., 'gpt-4o', 'sonnet-3.5'). Uses configured default if not provided."),
    api_base: str = typer.Option(None, "--api-base", help="Override the base URL for API calls (for OpenAI-compatible endpoints)."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the cache.")
):
    if sys.stdin.isatty():
        console.print("❌ Error: No log data piped.", style="bold red")
        raise typer.Exit(code=1)

    config = load_config()
    set_env_from_config(config)
    
    # Model selection
    if model:
        # If model is provided via CLI, resolve it
        # First check if it's an alias
        resolved_model = None
        for category, models in MODEL_CATEGORIES.items():
            if model in models:
                resolved_model = models[model]["full_id"]
                break
        
        if not resolved_model:
            # Not an alias, use as-is
            resolved_model = model
    else:
        # Use configured default
        resolved_model = config.get("default_model")
        if not resolved_model:
            console.print("❌ No default model configured. Run 'loglense configure' first.", style="bold red")
            raise typer.Exit(code=1)

    cache_hash, log_summary = parse_log_stream(sys.stdin)

    prompt_type = "error_summary" if log_summary['unique_errors'] else "general_summary"
    cache_key_string = f"{cache_hash}-{resolved_model}-{api_base or ''}-{prompt_type}"
    final_hash = hashlib.sha256(cache_key_string.encode()).hexdigest()
    cache_file = CACHE_DIR / final_hash

    if not no_cache and cache_file.is_file():
        console.print("✅ Found cached response.", style="bold green")
        console.print("\n--- 🤖 LogLense Cached Analysis ---")
        console.print(Markdown(cache_file.read_text())) 
        raise typer.Exit()

    try:
        # Provider selection logic
        if api_base:
            # Use OpenAI-compatible provider for custom base URLs
            provider = OpenAICompatibleProvider(model_name=resolved_model, base_url=api_base)
        else:
            provider_prefix, model_name_part = resolved_model.split('/', 1) if '/' in resolved_model else (None, resolved_model)
            
            if provider_prefix == "gemini":
                provider = GeminiProvider(model_name=model_name_part)
            elif provider_prefix == "openai":
                provider = OpenAIProvider(model_name=model_name_part)
            elif provider_prefix == "anthropic":
                provider = AnthropicProvider(model_name=model_name_part)
            elif provider_prefix == "ollama":
                # Ollama uses OpenAI-compatible API
                provider = OpenAICompatibleProvider(model_name=model_name_part, base_url="http://localhost:11434/v1")
            elif provider_prefix == "mistral":
                # Mistral uses OpenAI-compatible API
                provider = OpenAICompatibleProvider(model_name=model_name_part, base_url="https://api.mistral.ai/v1")
            elif provider_prefix == "deepseek":
                # DeepSeek uses OpenAI-compatible API
                provider = OpenAICompatibleProvider(model_name=model_name_part, base_url="https://api.deepseek.com/v1")
            else:
                # For unknown providers, suggest using --api-base
                console.print(f"❌ Unknown provider: {provider_prefix}", style="bold red")
                console.print("💡 Hint: For custom providers, use --api-base flag with the full model name.", style="yellow")
                raise typer.Exit(code=1)

        ai_analysis = None
        with Status("[bold cyan]🧠 Asking AI for insights...", console=console, spinner="dots"):
            ai_analysis = provider.get_summary(log_summary)
        
        console.print("\n--- 🤖 LogLense AI Analysis ---")
        console.print(Markdown(ai_analysis))

        if not no_cache:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(ai_analysis)
            console.print("\n📝 Analysis saved to cache.", style="dim")

    except Exception as e:
        console.print(f"\n❌ An unexpected error occurred: {e}", style="bold red")
        if "api_key" in str(e).lower():
            console.print("💡 Hint: Did you set the correct API key? Run 'loglense configure' to set it.", style="yellow")
        elif "Connection refused" in str(e):
            console.print("💡 Hint: Is your local model server running? For Ollama, check if it's running on port 11434.", style="yellow")
        raise typer.Exit(code=1)

@app.command(name="show-config", help="Show current configuration.")
def show_config_command():
    """Display current configuration."""
    config = load_config()
    
    console.print(Panel.fit("📋 Current Configuration", style="bold cyan"))
    console.print()
    
    if not config:
        console.print("[yellow]No configuration found. Run 'loglense configure' to set up.[/yellow]")
        return
    
    # Show default model
    default_model = config.get("default_model", "Not set")
    console.print(f"[bold]Default Model:[/bold] {default_model}")
    
    # Show configured API keys
    if "api_keys" in config and config["api_keys"]:
        console.print("\n[bold]Configured API Keys:[/bold]")
        for key, value in config["api_keys"].items():
            if value:
                masked = value[:8] + "..." + value[-4:] if len(value) > 12 else "***"
                console.print(f"  • {key}: {masked}")
    else:
        console.print("\n[yellow]No API keys configured.[/yellow]")
    
    console.print(f"\n[dim]Config file: {CONFIG_FILE}[/dim]")

@app.command(name="list-available-models", help="List all available models.")
def list_models_command():
    """Display all available models in a formatted table."""
    console.print(Panel.fit("📋 Available Models", style="bold cyan"))
    console.print()
    
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("Alias", style="green", width=15)
    table.add_column("Provider", style="cyan", width=12)
    table.add_column("Full ID", style="dim", width=30)
    table.add_column("Description", style="white")
    
    for category, models in MODEL_CATEGORIES.items():
        for model_alias, model_info in models.items():
            table.add_row(
                model_alias,
                category,
                model_info["full_id"],
                model_info["description"]
            )
    
    console.print(table)
    console.print()
    console.print("[dim]Use any alias with --model flag, e.g.: loglense analyze --model gpt-4o[/dim]")
    console.print("[dim]Or use the full ID for custom models, e.g.: loglense analyze --model openai/gpt-4-turbo[/dim]")

@cache_app.command(name="clear", help="Clear all cached log analyses.")
def cache_clear():
    """Deletes the cache directory."""
    if CACHE_DIR.exists():
        if typer.confirm(f"Are you sure you want to delete the cache at {CACHE_DIR}?"):
            shutil.rmtree(CACHE_DIR)
            console.print(f"✅ Cache cleared from [bold]{CACHE_DIR}[/bold].", style="green")
    else:
        console.print("✅ Cache directory does not exist. Nothing to clear.", style="green")

@cache_app.command(name="path", help="Show the path to the cache directory.")
def cache_path():
    """Prints the cache directory path."""
    console.print(f"Cache location: [bold]{CACHE_DIR}[/bold]")

@cache_app.command(name="size", help="Show the current size of the cache.")
def cache_size():
    """Calculates and prints the cache size."""
    if not CACHE_DIR.exists():
        console.print("Cache directory does not exist. Size is 0B.", style="green")
        raise typer.Exit()
    
    total_size = sum(f.stat().st_size for f in CACHE_DIR.glob('**/*') if f.is_file())
    human_readable_size = f"{total_size / 1024**2:.2f} MB" if total_size > 1024**2 else f"{total_size / 1024:.2f} KB"
    console.print(f"Total cache size: [bold]{human_readable_size}[/bold]")

if __name__ == "__main__":
    app()
