class Payload:
    def __init__(self, agent_name, prompt, greeting_message, model="gpt-4o"):
        self.agent_name = agent_name
        self.prompt = prompt
        self.first_message = greeting_message

        self.voice_provider = "elevenlabs"
        self.voice_id = "21m00Tcm4TlvDq8ikWAM"
        self.model = "eleven_turbo_v2_5"

        self.language = "en"

        self.llm_model = model
        self.llm_temperature = 0.7

    def get_payload(self):
        payload = {
            "name": self.agent_name,
            "config": {
                "prompt": self.prompt,
                "first_message": self.first_message,
                "language": self.language,
                "llm": {
                    "model": self.llm_model,
                    "temperature": self.llm_temperature,
                    "history_settings": {
                        "history_message_limit": 20,
                        "history_tool_result_limit": 5,
                    },
                },
                "voice": {
                    "provider": self.voice_provider,
                    "voice_id": self.voice_id,
                    "model": self.model,
                },
                "speech_to_text": {
                    "provider": "deepgram",
                    "multilingual": False,
                    "model": "nova-2",
                },
                "flow": {
                    "user_start_first": False,
                    "interruption": {
                        "allowed": True,
                        "keep_interruption_message": True,
                        "first_messsage": True,
                    },
                    "response_delay": {"generic_delay": 100, "number_input_delay": 100},
                    "inactivity_handling": {
                        "idle_time": 20,
                        "message": "Are you still there?",
                    },
                    "agent_terminate_call": {
                        "enabled": True,
                        "messages": ["Ending session. Thank you."],
                    },
                },
                "session_timeout": {
                    "message": "Ending session. Thank you.",
                    "max_duration": 3600,
                    "max_idle": 40,
                },
                "call_settings": {
                    "enable_recording": True,
                },
            },
        }
        return payload
