import click
import instructor
from openai import OpenAI
from groq import Groq
from anthropic import Anthropic
from pydantic import BaseModel
from typing import Any, Dict, List, Union, Optional
from junior.utils.setup import Setup
from junior.utils.docker_helper import DockerHelper
from junior.utils.token_tracker import TokenTracker
import tiktoken

class Brain:
    def __init__(self):
        """Initialize the Brain class."""
        click.echo("Initializing Brain...")
        self.setup = Setup()
        self.settings = self.setup.load_settings()
        self.docker_helper = DockerHelper()
        self.llm_configs = self.setup.llm_configs

        self.instructors = self.init_instructors()
        self.start_local_model_if_available()

    def init_instructors(self) -> Dict[str, Any]:
        """Initialize the instructor clients based on available API keys."""
        instructors = {}

        llm_settings = self.settings.get("LLM", {})
        remote_llms = llm_settings.get("remote", {})

        # Initialize remote instructors
        for full_name, api_key in remote_llms.items():
            if api_key:
                provider, _ = full_name.split("/")
                if provider.lower() == "openai":
                    instructors[full_name] = instructor.from_openai(OpenAI(api_key=api_key))
                elif provider.lower() == "ollama":
                    instructors[full_name] = instructor.from_openai(OpenAI(api_key="ollama", base_url="http://localhost:11434/v1"), mode=instructor.Mode.JSON)
                elif provider.lower() == "groq":
                    instructors[full_name] = instructor.from_groq(Groq(api_key=api_key))
                elif provider.lower() == "anthropic":
                    instructors[full_name] = instructor.from_anthropic(Anthropic().messages.create,mode=instructor.Mode.ANTHROPIC_JSON)

        # Add all local models that meet the system requirements
        local_llms = llm_settings.get("local", {})
        for full_name in local_llms.keys():
            if full_name in self.llm_configs and self.llm_configs[full_name]["local"]:
                instructors[full_name] = instructor.from_openai(OpenAI(api_key="ollama", base_url="http://localhost:11434/v1"), mode=instructor.Mode.JSON)
                #instructors[full_name] = Instruct(api_key=None, provider="ollama")

        return instructors

    def start_local_model_if_available(self):
        """Check, build, and run the local Ollama Docker instance if available."""
        llm_settings = self.settings.get("LLM", {}).get("local", {})

        if llm_settings:
            # TODO consume the existing methods for this on Setup.py
            click.echo("Local models available. Checking Ollama Docker instance...")
            if not self.docker_helper.container_exists(self.setup.local_container_name):
                click.echo("Starting Ollama Docker instance...")
                self.docker_helper.create_instance(name=self.setup.local_container_name, network="bridge")

            click.echo("Ensuring local models are available...")
            # Add your logic to ensure models are downloaded and available

    def count_tokens(self, prompt: str, model: str = "gpt-4") -> int:
        """Count tokens in the given prompt based on GPT-4.

        Args:
            prompt (str): The input prompt.
            model (str, optional): Model for counting tokens. Defaults to "gpt-4".

        Returns:
            int: The count of tokens.
        """
        encoding = tiktoken.encoding_for_model(model)
        return len(encoding.encode(prompt))

    def choose_best_instructor(self, prompt: str, category: Optional[str] = "everything") -> Optional[Any]:
        """Choose the best instructor for the given prompt and category.

        Args:
            prompt (str): Input prompt string.
            category (Optional[str], optional): Category of the task. Defaults to "everything".

        Returns:
            Optional[Instruct]: The most suitable instructor or None.
        """
        token_count = self.count_tokens(prompt)
        best_instructor = None
        best_match_score = float("-inf")

        for name, config in self.llm_configs.items():
            if name in self.instructors:
                if self.token_tracker.model_exceeds_limits(name, config["limits"]):
                    fallback = config["fallback"]
                    if fallback and fallback in self.instructors:
                        name = fallback
                        config = self.llm_configs[fallback]
                    else:
                        continue

                supports_category = category in config["expert_for"]
                within_token_limit = token_count + config["max_output_tokens"] <= config["context_window_tokens"]

                if supports_category and within_token_limit:
                    score = config["context_window_tokens"] - token_count  # Prioritize by remaining tokens
                    if score > best_match_score:
                        best_match_score = score
                        best_instructor = self.instructors[name]

        if best_instructor:
            click.echo(f"Selected instructor: {name}")
        else:
            click.echo("No suitable instructor found.")

        return best_instructor


    def prompt(self, prompt: str, output_schema: BaseModel, llm: str = None, category: Optional[str] = "everything") -> Union[BaseModel, None]:
        """Standardize calls to LLMs using a single prompt method.

        Args:
            prompt (str): Input prompt string.
            output_schema (BaseModel): Pydantic model to enforce the output schema.
            llm (str, optional): Specific LLM name to use. Defaults to None.
            category (Optional[str], optional): Category of the task. Defaults to "everything".

        Returns:
            Union[BaseModel, None]: The validated output schema instance or None.
        """
        if llm and llm.lower() in self.instructors:
            click.echo(f"Using specified LLM: {llm}")
            instructor = self.instructors[llm.lower()]
        else:
            instructor = self.choose_best_instructor(prompt, category)

        if not instructor:
            click.echo("No suitable LLM found.")
            return None

        tokens_used = self.count_tokens(prompt)
        response = instructor(prompt)
        self.token_tracker.update_model_usage(instructor.provider, tokens=tokens_used)

        try:
            validated_output = output_schema.parse_obj(response)
            return validated_output
        except Exception as e:
            click.echo(f"Error validating response: {e}")
            return None

# Example Pydantic output schema
class ExampleOutputSchema(BaseModel):
    summary: str
    points: List[str]

# Example usage
if __name__ == "__main__":
    brain = Brain()
    prompt_str = "Summarize the latest AI research papers."
    schema = ExampleOutputSchema

    result = brain.prompt(prompt_str, schema)
    if result:
        click.echo(result.model_dump_json(indent=4))
