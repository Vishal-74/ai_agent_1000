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
    COMPLETED = "completed"


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
        self.demo_completed = False  # Track if demo has been completed
        self.context = None  # Store the job context
        
    async def transition_to_processing(self):
        """Start background processing of requirements"""
        self.state = AgentState.PROCESSING
        logger.info("Transitioning to processing state")
        
        # Process requirements in background
        processing_agent = ProcessingAgent()
        self.processed_spec = await processing_agent.process_requirements(self.user_requirements)
        
        self.state = AgentState.DEMO_READY
        logger.info("Demo agent ready for handoff")
        
        # Notify Voxie that demo is ready
        if self.current_session and self.state == AgentState.DEMO_READY:
            await self.current_session.generate_reply(
                instructions=f"Perfect! Your {self.processed_spec.agent_type} is ready to test. Would you like to try it out now? Just say yes and I'll connect you to your demo agent!"
            )
        
    async def handoff_to_demo(self, room):
        """Handoff from Voxie to Demo agent"""
        if self.state == AgentState.DEMO_READY and self.processed_spec:
            logger.info("Handing off to demo agent")
            
            # PROPERLY STOP VOXIE SESSION FIRST
            if self.current_session:
                # Voxie announces handoff
                await self.current_session.generate_reply(
                    instructions=f"Perfect! I've created your {self.processed_spec.agent_type} and it's ready for testing. I'm now connecting you to your new agent. When you're done testing, just ask to speak with Voxie again and I'll be here to help with any adjustments. Here we go!"
                )
                
                # Wait longer for message delivery
                await asyncio.sleep(5)
                
                # PROPERLY CLOSE VOXIE SESSION
                await self.current_session.aclose()
                logger.info("Voxie session closed")
                
                # Clear current session
                self.current_session = None
            
            # Create demo agent with DIFFERENT VOICE (not coral)
            demo_agent = DemoAgentCreator.create_agent(self.processed_spec)
            
            # Create new session with demo agent (different voice from Voxie's coral)
            demo_session = AgentSession(
                llm=openai.realtime.RealtimeModel(voice=self.processed_spec.voice)
            )
            
            await demo_session.start(
                room=room,
                agent=demo_agent,
                room_input_options=RoomInputOptions(
                    noise_cancellation=noise_cancellation.BVC(),
                ),
            )
            
            # Wait a moment before demo agent speaks
            await asyncio.sleep(5)
            
            # Demo agent introduces itself
            business_name = self.processed_spec.business_context.get("business_name", "our business")
            await demo_session.generate_reply(
                instructions=f"Hello! I'm your new {self.processed_spec.agent_type} for {business_name}. I'm here to help you with your needs. You can try out my features - I can help with various services. When you're ready to go back to Voxie for feedback, just let me know. How can I assist you today?"
            )
            
            self.state = AgentState.DEMO_ACTIVE
            self.current_session = demo_session
            
    async def handoff_back_to_voxie(self, room):
        """Handoff from Demo back to Voxie"""
        if self.state == AgentState.DEMO_ACTIVE:
            logger.info("Handing back to Voxie")
            self.demo_completed = True  # Mark demo as completed
            
            # PROPERLY STOP DEMO SESSION
            if self.current_session:
                await self.current_session.generate_reply(
                    instructions="Thank you for testing me out! I'm now connecting you back to Voxie who can help with any changes or next steps. Have a great day!"
                )
                
                # Wait longer for message delivery
                await asyncio.sleep(5)
                
                # PROPERLY CLOSE DEMO SESSION
                await self.current_session.aclose()
                logger.info("Demo session closed")
                
                # Clear current session
                self.current_session = None
            
            # Create new Voxie session (always use coral voice for Voxie)
            voxie_session = AgentSession(
                llm=openai.realtime.RealtimeModel(voice="coral")  # Voxie's consistent voice
            )
            
            await voxie_session.start(
                room=room,
                agent=VoxieAgent(),
                room_input_options=RoomInputOptions(
                    noise_cancellation=noise_cancellation.BVC(),
                ),
            )
            
            # Wait a moment before Voxie speaks
            await asyncio.sleep(5)
            
            # Voxie comes back with context
            business_type = self.processed_spec.business_context.get("business_type", "custom")
            business_name = self.processed_spec.business_context.get("business_name", "your business")
            
            await voxie_session.generate_reply(
                instructions=f"Hi again! I'm Voxie, back to help you. How did the demo of your {business_type} agent for {business_name} go? Did it work as expected? Would you like to make any adjustments to the tone, functions, or anything else? Or if you're satisfied, I can help you close our session."
            )
            
            self.state = AgentState.VOXIE_ACTIVE
            self.current_session = voxie_session


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
        
        # Handle various requirement types that Voxie might use
        req_type_lower = requirement_type.lower()
        
        if "business_name" in req_type_lower or "name" in req_type_lower:
            agent_manager.user_requirements.business_name = value
        elif "business_type" in req_type_lower or "industry" in req_type_lower or "business_industry" in req_type_lower:
            agent_manager.user_requirements.business_type = value
        elif "cuisine" in req_type_lower:
            agent_manager.user_requirements.business_type = f"{value} Restaurant"
        elif "function" in req_type_lower or "agent_function" in req_type_lower:
            agent_manager.user_requirements.main_functions.extend(value.split(", "))
        elif "tone" in req_type_lower or "personality" in req_type_lower or "agent_tone" in req_type_lower:
            agent_manager.user_requirements.tone = value
        elif "audience" in req_type_lower or "target" in req_type_lower:
            agent_manager.user_requirements.target_audience = value
        elif "hours" in req_type_lower or "operating" in req_type_lower:
            agent_manager.user_requirements.special_requirements.append(f"Operating hours: {value}")
        elif "special" in req_type_lower or "requirement" in req_type_lower:
            agent_manager.user_requirements.special_requirements.append(value)
        elif "contact" in req_type_lower:
            contact_type = req_type_lower.replace("contact_", "")
            agent_manager.user_requirements.contact_info[contact_type] = value
        else:
            # Generic storage for any other requirement types
            agent_manager.user_requirements.special_requirements.append(f"{requirement_type}: {value}")
            
        return f"Stored {requirement_type}: {value}"

    @function_tool
    async def finalize_requirements(self):
        """Finalize requirements and start processing"""
        logger.info("Finalizing requirements and starting processing")
        
        # Start background processing
        asyncio.create_task(agent_manager.transition_to_processing())
        
        return "Perfect! I have all the information I need. Give me a few seconds to process everything and create your custom voice AI agent..."
    
    @function_tool
    async def start_demo(self):
        """Start the demo agent"""
        if agent_manager.state == AgentState.DEMO_READY:
            logger.info("Voxie triggering demo handoff")
            asyncio.create_task(agent_manager.handoff_to_demo(agent_manager.room))
            return "Great! Let me connect you to your demo agent now..."
        else:
            return "I'm still working on creating your agent. Just a moment more..."
    
    @function_tool
    async def try_demo_again(self):
        """Allow user to try the demo again after feedback"""
        if agent_manager.demo_completed and agent_manager.processed_spec:
            logger.info("Voxie triggering second demo handoff")
            agent_manager.state = AgentState.DEMO_READY  # Reset state for demo
            asyncio.create_task(agent_manager.handoff_to_demo(agent_manager.room))
            return "Perfect! Let me connect you to your updated demo agent now..."
        else:
            return "Let me first process your requirements and create your demo agent."
    
    @function_tool
    async def close_session(self):
        """Close the session gracefully"""
        logger.info("Voxie closing session at user request")
        agent_manager.state = AgentState.COMPLETED
        
        if agent_manager.processed_spec:
            business_name = agent_manager.processed_spec.business_context.get("business_name", "your business")
            return f"Thank you for using Voxie to create your custom voice AI agent for {business_name}! Your agent specifications have been saved and you can come back anytime to test or make changes. Have a wonderful day!"
        else:
            return "Thank you for using Voxie! Feel free to come back anytime when you're ready to create your custom voice AI agent. Have a great day!"
    
    @function_tool
    async def ask_demo_preference(self):
        """Ask user if they want to try the demo or are satisfied"""
        if agent_manager.demo_completed:
            return "Would you like to try the demo again with any updates, or are you satisfied with your agent? If you're all set, just let me know and I can close our session for you."
        else:
            return "Your agent is ready! Would you like to try the demo now, or do you have any questions first?"


class ProcessingAgent:
    """Background agent that processes requirements into structured data"""
    
    BUSINESS_TEMPLATES = {
        "restaurant": {
            "voice": "alloy",  # Different from Voxie's coral
            "tone": "friendly and welcoming",
            "functions": ["take_reservation", "menu_inquiry", "take_order"],
            "sample_responses": [
                "Thank you for calling {business_name}! How can I help you today?",
                "I'd be happy to help you make a reservation.",
                "Let me check our menu for you."
            ]
        },
        "dental": {
            "voice": "nova",  # Different from Voxie's coral
            "tone": "professional and reassuring",
            "functions": ["schedule_appointment", "check_insurance", "emergency_info"],
            "sample_responses": [
                "Thank you for calling {business_name}. How may I assist you today?",
                "I can help you schedule an appointment.",
                "Let me check your insurance coverage."
            ]
        },
        "pizza": {
            "voice": "echo",  # Different from Voxie's coral
            "tone": "casual and enthusiastic", 
            "functions": ["take_order", "menu_inquiry", "take_reservation"],
            "sample_responses": [
                "Hey there! Welcome to {business_name}! Ready to order some amazing pizza?",
                "What can I get started for you today?",
                "Let me tell you about our daily specials!"
            ]
        },
        "retail": {
            "voice": "alloy",  # Different from Voxie's coral
            "tone": "helpful and professional",
            "functions": ["check_product_availability", "store_hours"],
            "sample_responses": [
                "Hello and welcome to {business_name}! How can I help you today?",
                "I can help you find what you're looking for.",
                "Let me check if we have that in stock for you."
            ]
        },
        "medical": {
            "voice": "nova",  # Different from Voxie's coral
            "tone": "caring and professional", 
            "functions": ["schedule_appointment", "check_insurance", "emergency_info"],
            "sample_responses": [
                "Thank you for calling {business_name}. How may I help you today?",
                "I can assist you with scheduling an appointment.",
                "Let me help you with your medical needs."
            ]
        },
        "general": {
            "voice": "alloy",  # Different from Voxie's coral
            "tone": "friendly and professional",
            "functions": ["general_inquiry", "business_hours"],
            "sample_responses": [
                "Hello! Thank you for contacting {business_name}. How can I help you?",
                "I'm here to assist you with any questions you may have.",
                "How can I help you today?"
            ]
        }
    }
    
    async def process_requirements(self, requirements: UserRequirements) -> ProcessedAgentSpec:
        """Convert user requirements into structured agent specification"""
        logger.info("Processing user requirements...")
        
        # Simulate processing time
        await asyncio.sleep(5)
        
        # Determine business type from requirements
        business_type = "general"  # Default
        if requirements.business_type:
            btype_lower = requirements.business_type.lower()
            if "restaurant" in btype_lower or "indian" in btype_lower or "food" in btype_lower:
                business_type = "restaurant"
            elif "dental" in btype_lower or "dentist" in btype_lower:
                business_type = "dental"
            elif "pizza" in btype_lower:
                business_type = "pizza"
            elif "retail" in btype_lower or "store" in btype_lower or "shop" in btype_lower:
                business_type = "retail"
            elif "medical" in btype_lower or "clinic" in btype_lower or "doctor" in btype_lower or "healthcare" in btype_lower or "hospital" in btype_lower:
                business_type = "medical"
        
        template = self.BUSINESS_TEMPLATES.get(business_type, self.BUSINESS_TEMPLATES["general"])
        
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
        
        # Get business type to determine which agent class to create
        business_type = spec.business_context.get("business_type", "general")
        
        if "restaurant" in business_type.lower() or "pizza" in business_type.lower():
            return DemoAgentCreator._create_restaurant_agent(spec)
        elif "dental" in business_type.lower() or "medical" in business_type.lower():
            return DemoAgentCreator._create_medical_agent(spec)
        elif "retail" in business_type.lower():
            return DemoAgentCreator._create_retail_agent(spec)
        else:
            return DemoAgentCreator._create_general_agent(spec)
    
    @staticmethod
    def _create_restaurant_agent(spec: ProcessedAgentSpec):
        """Create restaurant-specific demo agent"""
        class RestaurantDemoAgent(Agent):
            def __init__(self):
                super().__init__(instructions=spec.instructions)
                
            @function_tool
            async def take_reservation(self, date: str, time: str, party_size: str, name: str, phone: str = ""):
                """Take a restaurant reservation"""
                business_name = spec.business_context.get("business_name", "our restaurant")
                logger.info(f"Demo agent taking reservation: {name} for {party_size} people on {date} at {time}")
                return f"Perfect! I've made a reservation at {business_name} for {name} for {party_size} people on {date} at {time}. We'll see you then!"
            
            @function_tool
            async def menu_inquiry(self, item: str = ""):
                """Handle menu inquiries"""
                logger.info(f"Demo agent handling menu inquiry for: {item}")
                if item:
                    return f"Great choice! Our {item} is one of our most popular dishes. It's made with fresh ingredients and comes with a side of your choice."
                else:
                    return f"We have a wonderful menu. What would you like to know more about?"
            
            @function_tool
            async def take_order(self, items: str, customer_name: str, phone: str = "", address: str = ""):
                """Take a food order"""
                business_name = spec.business_context.get("business_name", "our restaurant")
                logger.info(f"Demo agent taking order: {items} for {customer_name}")
                return f"Excellent! I've got your order for {items}. That'll be ready in about 25-30 minutes at {business_name}. Thank you!"
                
            @function_tool
            async def end_demo(self):
                """End the demo session"""
                return "Thank you for trying out the demo! This gives you an idea of how your custom voice agent would work. Would you like to speak with Voxie again to make adjustments or discuss next steps?"
            
            @function_tool
            async def handoff_to_voxie(self):
                """Handoff back to Voxie for feedback"""
                logger.info("Demo agent requesting handoff back to Voxie")
                # Trigger handoff in manager
                asyncio.create_task(agent_manager.handoff_back_to_voxie(agent_manager.room))
                return "Let me connect you back to Voxie now..."
        
        return RestaurantDemoAgent()
    
    @staticmethod
    def _create_medical_agent(spec: ProcessedAgentSpec):
        """Create medical/dental-specific demo agent"""
        class MedicalDemoAgent(Agent):
            def __init__(self):
                super().__init__(instructions=spec.instructions)
                
            @function_tool
            async def schedule_appointment(self, date: str, time: str, patient_name: str, phone: str, service_type: str = "consultation"):
                """Schedule a medical/dental appointment"""
                business_name = spec.business_context.get("business_name", "our facility")
                logger.info(f"Demo agent scheduling appointment: {patient_name} on {date} at {time}")
                return f"I've scheduled your {service_type} appointment at {business_name} for {patient_name} on {date} at {time}. We'll send you a confirmation shortly."
            
            @function_tool
            async def check_insurance(self, insurance_provider: str, member_id: str = ""):
                """Check insurance coverage"""
                logger.info(f"Demo agent checking insurance: {insurance_provider}")
                return f"I can help you verify your {insurance_provider} coverage. We are in-network with most major providers. Let me check your benefits."
            
            @function_tool
            async def emergency_info(self):
                """Provide emergency contact information"""
                return "For emergencies outside business hours, please call our emergency line. If this is a medical emergency, please call 911 immediately."
                
            @function_tool
            async def end_demo(self):
                """End the demo session"""
                return "Thank you for trying out the demo! This gives you an idea of how your custom voice agent would work. Would you like to speak with Voxie again to make adjustments or discuss next steps?"
            
            @function_tool
            async def handoff_to_voxie(self):
                """Handoff back to Voxie for feedback"""
                logger.info("Demo agent requesting handoff back to Voxie")
                # Trigger handoff in manager
                asyncio.create_task(agent_manager.handoff_back_to_voxie(agent_manager.room))
                return "Let me connect you back to Voxie now..."
        
        return MedicalDemoAgent()
    
    @staticmethod
    def _create_retail_agent(spec: ProcessedAgentSpec):
        """Create retail-specific demo agent"""
        class RetailDemoAgent(Agent):
            def __init__(self):
                super().__init__(instructions=spec.instructions)
                
            @function_tool
            async def check_product_availability(self, product_name: str):
                """Check if a product is in stock"""
                logger.info(f"Demo agent checking product availability: {product_name}")
                return f"Let me check our inventory for {product_name}. Yes, we have that in stock! Would you like me to hold one for you?"
            
            @function_tool
            async def store_hours(self):
                """Provide store hours"""
                return "We're open Monday through Saturday 9 AM to 8 PM, and Sunday 11 AM to 6 PM."
                
            @function_tool
            async def end_demo(self):
                """End the demo session"""
                return "Thank you for trying out the demo! This gives you an idea of how your custom voice agent would work. Would you like to speak with Voxie again to make adjustments or discuss next steps?"
            
            @function_tool
            async def handoff_to_voxie(self):
                """Handoff back to Voxie for feedback"""
                logger.info("Demo agent requesting handoff back to Voxie")
                # Trigger handoff in manager
                asyncio.create_task(agent_manager.handoff_back_to_voxie(agent_manager.room))
                return "Let me connect you back to Voxie now..."
        
        return RetailDemoAgent()
    
    @staticmethod
    def _create_general_agent(spec: ProcessedAgentSpec):
        """Create general business demo agent"""
        class GeneralDemoAgent(Agent):
            def __init__(self):
                super().__init__(instructions=spec.instructions)
                
            @function_tool
            async def general_inquiry(self, topic: str, customer_name: str = ""):
                """Handle general business inquiries"""
                business_name = spec.business_context.get("business_name", "our business")
                logger.info(f"Demo agent handling general inquiry: {topic}")
                return f"Thank you for contacting {business_name}! I'd be happy to help you with {topic}. Let me provide you with that information."
            
            @function_tool
            async def business_hours(self):
                """Provide business hours"""
                return "We're typically open Monday through Friday 9 AM to 5 PM. Would you like me to check our specific hours for today?"
                
            @function_tool
            async def end_demo(self):
                """End the demo session"""
                return "Thank you for trying out the demo! This gives you an idea of how your custom voice agent would work. Would you like to speak with Voxie again to make adjustments or discuss next steps?"
            
            @function_tool
            async def handoff_to_voxie(self):
                """Handoff back to Voxie for feedback"""
                logger.info("Demo agent requesting handoff back to Voxie")
                # Trigger handoff in manager
                asyncio.create_task(agent_manager.handoff_back_to_voxie(agent_manager.room))
                return "Let me connect you back to Voxie now..."
        
        return GeneralDemoAgent()


async def entrypoint(ctx: agents.JobContext):
    """Main entry point - starts with Voxie agent"""
    logger.info("Starting multi-agent system with Voxie")
    
    # Store room and context reference for handoffs
    agent_manager.room = ctx.room
    agent_manager.context = ctx
    
    # Start with Voxie agent
    session = AgentSession(
        llm=openai.realtime.RealtimeModel(voice="coral")  # Voxie's voice
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
        instructions="Greet the user warmly as Voxie in English and explain that you help create custom voice AI agents for their business. Ask them what type of business they want to create an agent for."
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))