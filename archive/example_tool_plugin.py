"""
Example tool plugin that demonstrates how to add tools for function calling.
This plugin provides a weather tool example.

UNSUPPORTED!! TOOL SCAFFOLDING NOT READY
"""
from typing import Dict, Any
import json

class WeatherToolPlugin:
    """Example tool plugin for weather information"""

    def get_schema(self) -> Dict[str, Any]:
        """Return OpenAI function schema for weather tool"""
        return {
            "name": "get_weather",
            "description": "Get current weather information for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA"
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "The temperature unit to use"
                    }
                },
                "required": ["location"]
            }
        }

    async def execute(self, arguments: Dict[str, Any]) -> Any:
        """Execute the weather tool"""
        location = arguments.get("location")
        unit = arguments.get("unit", "fahrenheit")

        # This is a mock implementation
        # In reality, you'd call a weather API here
        mock_weather_data = {
            "location": location,
            "temperature": 72 if unit == "fahrenheit" else 22,
            "unit": unit,
            "condition": "sunny",
            "humidity": 65,
            "wind_speed": 5
        }

        return {
            "status": "success",
            "data": mock_weather_data
        }
