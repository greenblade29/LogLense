import os
import google.generativeai as genai
import openai
import anthropic

from abc import ABC, abstractmethod

# This helper function creates the prompt and is shared by all providers
def _create_prompt(log_summary: dict) -> str:
    """Helper function to create a standardized or general-purpose prompt."""
    
    # If we found specific errors, use the detailed error prompt
    if log_summary['unique_errors']:
        error_lines = "\n".join([f"- (Count: {count}) {error}" for error, count in log_summary['unique_errors'].items()])
        return f"""
        You are an expert SRE analyzing a log file summary. The following explicit errors were found. Provide a brief, professional analysis.

        Log Summary:
        - Total Lines: {log_summary['total_lines']}
        - Unique Errors Found:
        {error_lines}

        Analysis:
        1. **Likely Root Cause:**
        2. **Sequence of Events:**
        3. **Recommended Next Steps:**
        """
    # If no specific errors were found, use the general analysis prompt
    else:
        first_lines = "\n".join(log_summary['first_lines'])
        last_lines = "\n".join(log_summary['last_lines'])
        return f"""
        You are an expert SRE analyzing a log file. No lines with explicit 'ERROR' or 'CRITICAL' keywords were found.
        However, there might still be an issue. Analyze the following snippets from the beginning and end of the log file for any anomalies, stack traces, or other significant events.

        If you find a likely issue, provide a brief analysis. If the log appears normal (e.g., clean shutdown), state that.

        --- First 20 Lines ---
        {first_lines}

        --- Last 20 Lines ---
        {last_lines}
        
        Analysis:
        """

# --- Base Class ---
class BaseProvider(ABC):
    @abstractmethod
    def get_summary(self, log_summary: dict) -> str:
        pass

# --- Specific Providers ---
class GeminiProvider(BaseProvider):
    def __init__(self, model_name: str):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set.")
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(model_name)

    def get_summary(self, log_summary: dict) -> str:
        prompt = _create_prompt(log_summary)
        response = self.model.generate_content(prompt)
        return response.text

class OpenAIProvider(BaseProvider):
    def __init__(self, model_name: str):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set.")
        self.client = openai.OpenAI(api_key=self.api_key)
        self.model_name = model_name

    def get_summary(self, log_summary: dict) -> str:
        prompt = _create_prompt(log_summary)
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content

class AnthropicProvider(BaseProvider):
    def __init__(self, model_name: str):
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set.")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model_name = model_name

    def get_summary(self, log_summary: dict) -> str:
        prompt = _create_prompt(log_summary)
        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

# --- Generic Providers ---
class OpenAICompatibleProvider(BaseProvider):
    """Provider for any model served with an OpenAI-compatible API (Ollama, Mistral, DeepSeek, etc.)."""
    def __init__(self, model_name: str, base_url: str, api_key: str = None):
        # Determine if we need an API key based on the service
        if api_key:
            final_api_key = api_key
        elif "mistral.ai" in base_url:
            final_api_key = os.getenv("MISTRAL_API_KEY")
            if not final_api_key:
                raise ValueError("MISTRAL_API_KEY environment variable not set.")
        elif "deepseek.com" in base_url:
            final_api_key = os.getenv("DEEPSEEK_API_KEY")
            if not final_api_key:
                raise ValueError("DEEPSEEK_API_KEY environment variable not set.")
        else:
            final_api_key = "not-needed"  # For local services like Ollama
        
        self.client = openai.OpenAI(
            base_url=base_url, 
            api_key=final_api_key,
            timeout=300.0,  # 5 minute timeout for long-running requests
            max_retries=1
        )
        self.model_name = model_name

    def get_summary(self, log_summary: dict) -> str:
        prompt = _create_prompt(log_summary)
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
