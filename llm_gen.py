import json
from openai import OpenAI
from dotenv import load_dotenv
import os

class LLMScenarioGenerator:
    def __init__(self, api_key=None):
        self.client = OpenAI(api_key=api_key,
    base_url="https://api.ai.kodekloud.com/v1")

    def generate_scenario(self):
        prompt = """
        Generate a traffic scenario for a 4-way junction.

        Return ONLY valid JSON in this format:
        {
          "traffic_level": "low | medium | high",
          "spawn_rate": float between 0.1 and 1.0,
          "heavy_directions": list of directions from ["N","S","E","W"],
          "variation": "balanced | uneven | rush_hour"
        }

        Rules:
        - High traffic → higher spawn_rate
        - Rush hour → spawn_rate > 0.7
        - Uneven → only 1 heavy direction
        - Balanced → 2 or more heavy directions
        """

        response = self.client.responses.create(
            model="gpt-5.2",
            input=prompt
        )

        text = response.output[0].content[0].text

        try:
            scenario = json.loads(text)
        except Exception:
            print("Invalid JSON from LLM, using fallback...")
            scenario = self.fallback()

        return self.validate(scenario)

    def validate(self, scenario):
        default = {
            "traffic_level": "medium",
            "spawn_rate": 0.5,
            "heavy_directions": ["N", "S"],
            "variation": "balanced"
        }

        try:
            level = scenario.get("traffic_level", default["traffic_level"])
            spawn_rate = float(scenario.get("spawn_rate", default["spawn_rate"]))
            heavy_dirs = scenario.get("heavy_directions", default["heavy_directions"])
            variation = scenario.get("variation", default["variation"])

            spawn_rate = max(0.1, min(spawn_rate, 1.0))

            if not isinstance(heavy_dirs, list) or len(heavy_dirs) == 0:
                heavy_dirs = default["heavy_directions"]

            return {
                "traffic_level": level,
                "spawn_rate": spawn_rate,
                "heavy_directions": heavy_dirs,
                "variation": variation
            }

        except Exception:
            return default

    def fallback(self):
        return {
            "traffic_level": "medium",
            "spawn_rate": 0.5,
            "heavy_directions": ["N", "E"],
            "variation": "balanced"
        }


if __name__ == "__main__":
    load_dotenv()

    key = os.getenv("OPENAI_API_KEY")
    generator = LLMScenarioGenerator(api_key=key)

    scenario = generator.generate_scenario()
    print("Generated Scenario:")
    print(json.dumps(scenario, indent=4))