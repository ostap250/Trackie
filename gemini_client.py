"""
gemini_client.py — Wrapper around Google Gemini API for food analysis.
"""

import json
import google.generativeai as genai


class GeminiClient:
    def __init__(self, api_key: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")

    def analyze_food(self, description: str) -> dict:
        """
        Ask Gemini to estimate calories and protein for a food description.
        Returns: {"calories": float, "protein": float} or raises on error.
        """
        prompt = f"""You are a nutrition expert. Analyze the following meal description and estimate its nutritional content.

Meal: "{description}"

Respond with ONLY a valid JSON object in this exact format (no markdown, no extra text):
{{"calories": <number>, "protein": <number>}}

Where:
- calories = total kilocalories (kcal)
- protein = total protein in grams

Be realistic with estimates. If multiple items, sum them all up."""

        response = self.model.generate_content(prompt)
        raw = response.text.strip()

        # Strip markdown code fences if Gemini wraps the response
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        data = json.loads(raw)
        return {
            "calories": float(data["calories"]),
            "protein": float(data["protein"]),
        }
