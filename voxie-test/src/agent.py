from dotenv import load_dotenv
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import json
import asyncio
import logging

from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.agents.llm import function_tool
from livekit.plugins import openai, noise_cancellation

load_dotenv(".env.local")
logger = logging.getLogger("multi-agent")


class AgentState(Enum):
    VOXIE_ACTIVE = "voxie"
    PROCESSING = "processing"
    DEMO_READY = "demo_ready"
    DEMO_ACTIVE = "demo"


@dataclass
class UserRequirements:
    business_type: Optional[str] = None
    business_name: Optional[str] = None
    target_audience: Optional[str] = None
    main_functions: List[str] = field(default_factory=list)
    tone: Optional[str] = None
    special_requirements: List[str] = field(default_factory=list)
    contact_info: Dict[str, str] = field(default_factory=dict)


@dataclass
class ProcessedAgentSpec:
    agent_type: str
    instructions: str
    voice: str
    functions: List[Dict[str, Any]]
    sample_responses: List[str]
    business_context: Dict[str, Any]


class AgentManager:
    def __init__(self):
        self.state = AgentState.VOXIE_ACTIVE
        self.user_requirements = UserRequirements()
        self.processed_spec: Optional[ProcessedAgentSpec] = None
        self.current_session: Optional[AgentSession] = None
        self.room = None
        
    async def transition_to_processing(self):
        """Start background processing of requirements"""
        self.state = AgentState.PROCESSING
        logger.info("Transitioning to processing state")
        
        # Process requirements in background
        processing_agent = ProcessingAgent()
        self.processed_spec = await processing_agent.process_requirements(self.user_requirements)
        
        self.state = AgentState.DEMO_READY
        logger.info("Demo agent ready for handoff")
        
    async def handoff_to_demo(self, ctx: agents.JobContext):
        """Handoff from Voxie to Demo agent"""
        if self.state == AgentState.DEMO_READY and self.processed_spec:
            logger.info("Handing off to demo agent")
            
            # Create demo agent
            demo_agent = DemoAgentCreator.create_agent(self.processed_spec)
            
            # Create new session with demo agent
            demo_session = AgentSession(
                llm=openai.realtime.RealtimeModel(voice="coral")
            )
            
            await demo_session.start(
                room=ctx.room,
                agent=demo_agent,
                room_input_options=RoomInputOptions(
                    noise_cancellation=noise_cancellation.BVC(),
                ),
            )
            
            # Generate handoff message
            await demo_session.generate_reply(
                instructions=f"You are now the {self.processed_spec.agent_type} agent. Introduce yourself and start helping the user."
            )
            
            self.state = AgentState.DEMO_ACTIVE
            self.current_session = demo_session


# Global agent manager
agent_manager = AgentManager()


class VoxieAgent(Agent):
    """Manager agent that collects user requirements"""
    
    def __init__(self):
        super().__init__(
            instructions="""You are Voxie, a friendly AI assistant helping users create custom voice AI agents for their business. 

Your job is to:
1. Understand what type of business voice agent they want to create
2. Gather all necessary information about their requirements
3. Guide them through the process step by step

Ask about:
- Business type (restaurant, dental office, pizza shop, customer service, etc.)
- Business name and basic details
- What functions they want (booking, ordering, FAQ, etc.)
- Tone and personality they prefer
- Any special requirements

Be conversational, helpful, and thorough. Once you have enough information, use the finalize_requirements function."""
        )

    @function_tool
    async def store_user_requirement(self, requirement_type: str, value: str):
        """Store a user requirement during the conversation"""
        logger.info(f"Storing requirement: {requirement_type} = {value}")
        
        if requirement_type == "business_type":
            agent_manager.user_requirements.business_type = value
        elif requirement_type == "business_name":
            agent_manager.user_requirements.business_name = value
        elif requirement_type == "target_audience":
            agent_manager.user_requirements.target_audience = value
        elif requirement_type == "tone":
            agent_manager.user_requirements.tone = value
        elif requirement_type == "function":
            agent_manager.user_requirements.main_functions.append(value)
        elif requirement_type == "special_requirement":
            agent_manager.user_requirements.special_requirements.append(value)
        elif requirement_type.startswith("contact_"):
            contact_type = requirement_type.split("_")[1]
            agent_manager.user_requirements.contact_info[contact_type] = value
            
        return f"Stored {requirement_type}: {value}"

    @function_tool
    async def finalize_requirements(self):
        """Finalize requirements and start processing"""
        logger.info("Finalizing requirements and starting processing")
        
        # Start background processing
        asyncio.create_task(agent_manager.transition_to_processing())
        
        return "Perfect! I have all the information I need. Give me a few seconds to process everything and create your custom voice AI agent..."


class ProcessingAgent:
    """Background agent that processes requirements into structured data"""
    
    BUSINESS_TEMPLATES = {
        "restaurant": {
            "voice": "coral",
            "tone": "friendly and welcoming",
            "functions": ["take_reservation", "menu_inquiry", "hours_info"],
            "sample_responses": [
                "Thank you for calling {business_name}! How can I help you today?",
                "I'd be happy to help you make a reservation.",
                "Let me check our menu for you."
            ]
        },
        "dental": {
            "voice": "nova",
            "tone": "professional and reassuring",
            "functions": ["schedule_appointment", "insurance_check", "emergency_info"],
            "sample_responses": [
                "Thank you for calling {business_name}. How may I assist you today?",
                "I can help you schedule an appointment with Dr. Smith.",
                "Let me check your insurance coverage."
            ]
        },
        "pizza": {
            "voice": "alloy",
            "tone": "casual and enthusiastic",
            "functions": ["take_order", "track_order", "menu_info", "deals_info"],
            "sample_responses": [
                "Hey there! Welcome to {business_name}! Ready to order some amazing pizza?",
                "What can I get started for you today?",
                "Let me tell you about our daily specials!"
            ]
        }
    }
    
    async def process_requirements(self, requirements: UserRequirements) -> ProcessedAgentSpec:
        """Convert user requirements into structured agent specification"""
        logger.info("Processing user requirements...")
        
        # Simulate processing time
        await asyncio.sleep(2)
        
        # Get business template or create custom
        business_type = requirements.business_type.lower() if requirements.business_type else "general"
        template = self.BUSINESS_TEMPLATES.get(business_type, self.BUSINESS_TEMPLATES["restaurant"])
        
        # Build agent specification
        spec = ProcessedAgentSpec(
            agent_type=f"{requirements.business_name or 'Custom'} {business_type.title()} Assistant",
            instructions=self._build_instructions(requirements, template),
            voice=template["voice"],
            functions=self._build_functions(requirements, template),
            sample_responses=[
                resp.format(business_name=requirements.business_name or "our business")
                for resp in template["sample_responses"]
            ],
            business_context={
                "business_type": business_type,
                "business_name": requirements.business_name,
                "tone": requirements.tone or template["tone"],
                "functions": requirements.main_functions
            }
        )
        
        logger.info(f"Generated spec for {spec.agent_type}")
        return spec
    
    def _build_instructions(self, req: UserRequirements, template: Dict) -> str:
        """Build comprehensive agent instructions"""
        business_name = req.business_name or "the business"
        tone = req.tone or template["tone"]
        
        instructions = f"""You are a {tone} AI assistant for {business_name}.
        
Your primary responsibilities:
- Assist customers with their needs
- Provide helpful information about our services
- Maintain a {tone} demeanor at all times"""

        if req.main_functions:
            instructions += f"\n\nKey functions you can help with:\n"
            for func in req.main_functions:
                instructions += f"- {func}\n"
                
        if req.special_requirements:
            instructions += f"\n\nSpecial requirements:\n"
            for req_item in req.special_requirements:
                instructions += f"- {req_item}\n"
                
        return instructions
    
    def _build_functions(self, req: UserRequirements, template: Dict) -> List[Dict[str, Any]]:
        """Build function definitions for the agent"""
        functions = []
        
        # Add template functions
        for func_name in template["functions"]:
            if func_name == "take_reservation":
                functions.append({
                    "name": "take_reservation",
                    "description": "Take a restaurant reservation",
                    "parameters": ["date", "time", "party_size", "name", "phone"]
                })
            elif func_name == "schedule_appointment":
                functions.append({
                    "name": "schedule_appointment", 
                    "description": "Schedule a dental appointment",
                    "parameters": ["date", "time", "service_type", "patient_name", "phone"]
                })
            elif func_name == "take_order":
                functions.append({
                    "name": "take_order",
                    "description": "Take a pizza order",
                    "parameters": ["items", "size", "quantity", "customer_info", "delivery_address"]
                })
                
        return functions


class DemoAgentCreator:
    """Creates demo agents from processed specifications"""
    
    @staticmethod
    def create_agent(spec: ProcessedAgentSpec) -> Agent:
        """Create a demo agent from the specification"""
        logger.info(f"Creating demo agent: {spec.agent_type}")
        
        class GeneratedDemoAgent(Agent):
            def __init__(self):
                super().__init__(instructions=spec.instructions)
                
            @function_tool
            async def handle_business_function(self, function_name: str, **kwargs):
                """Generic function handler for business operations"""
                logger.info(f"Demo agent handling: {function_name} with {kwargs}")
                
                if "reservation" in function_name or "appointment" in function_name:
                    return f"I've scheduled your {function_name} for {kwargs.get('date', 'the requested date')} at {kwargs.get('time', 'the requested time')}. We'll see you then!"
                elif "order" in function_name:
                    return f"Great! I've got your order for {kwargs.get('items', 'your items')}. Your total comes to $25.99. We'll have that ready for you soon!"
                elif "info" in function_name or "menu" in function_name:
                    return f"Here's the information you requested about {kwargs.get('topic', 'our services')}. Is there anything specific you'd like to know more about?"
                else:
                    return f"I've processed your request for {function_name}. How else can I help you today?"
                    
            @function_tool
            async def end_demo(self):
                """End the demo session"""
                return "Thank you for trying out the demo! This gives you an idea of how your custom voice agent would work. Would you like to make any adjustments or are you ready to proceed?"
        
        return GeneratedDemoAgent()


async def entrypoint(ctx: agents.JobContext):
    """Main entry point - starts with Voxie agent"""
    logger.info("Starting multi-agent system with Voxie")
    
    # Store room reference for handoffs
    agent_manager.room = ctx.room
    
    # Start with Voxie agent
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="coral")
    )
    
    agent_manager.current_session = session
    
    await session.start(
        room=ctx.room,
        agent=VoxieAgent(),
        room_input_options=RoomInputOptions(
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await session.generate_reply(
        instructions="Greet the user in English and start gathering requirements for their custom voice AI agent."
    )
    
    # Monitor for state changes and handle handoffs
    while True:
        await asyncio.sleep(1)
        
        if agent_manager.state == AgentState.DEMO_READY:
            # Notify user that demo is ready
            await session.generate_reply(
                instructions="Tell the user their custom agent is ready and ask if they'd like to test it out now."
            )
            
            # Wait a moment then handoff
            await asyncio.sleep(3)
            await agent_manager.handoff_to_demo(ctx)
            break


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
