#############################################################################################################################

# [Webhook] ──> [FastAPI Endpoint -> Instantly return 200 OK status to Webhook] -> SubTasks 
#       SubTasks: Fetch Calendar via Injected Client ──> Agent Filters/Extracts ──> Trigger Booking Workflow

# [Booking In Progress] or [Booked: Confirmation #1234]) or log the processed event_id in a database.

############################################################################################################################

# Business Rules: need to be contained in system prompts:
# Short flight time: 2 hours of less
# Normal flight time: 2 - 4 hours
# Long flight time: 4 - 6 hours
# International flight time: 6+ hours

# Flight classes
#   First Class
#   Business Class
#   Premium Class
#   Domestic Class - only if booked with extra leg room.

# Booking Flight Classes
# 1) The model should book only one flight there and one flight back. However, details of alternative flights must be added to the calendar event, keep details of flights details either side of optimal incase of a need to manually rebook,
# 2) For all International Flights, the booking should be in First Class
# 3) For all long haul flights, the booking should be in Business Class
# 4) for all normal flight durations, the booking should be in Domestic class, but with extra leg room.
# 5) for short flights, Domestic class, with or without extra leg room should be allowed.
# ** Only if the minimum booking class is not available for that flight, should the next grade up be considered. 

# Journey time calculation - Flight booking and location of Hotel rules. A flight would have to be booked on the following basis: 
#
#   CALENDAR MEETINGS: It is assumed that all meetings must take place between 9am and 6pm. If they are booked in the diary outside of these times, it should be flagged for checking before any bookings are made. 
#   FLIGHT BOOKINGS: The Optimal flight to book could only be the same day as the meeting if: the flight is short (less than 2 hours), and the meeting is from 2pm onwards, and the trip from the airport by taxi to the meeting venue is less than 90 minutes, and it is feasibly possible for the attendee to arrive at the venue a minimum of 30 minutes before the meeting start ->then it would be possible to book the flight for early morning on the same day as the meeting. 
#       Example: Assuming a meeting time of 2pm. Meeting destination arrival 13.30. Taxi from Airport booked for 12 noon. Landing scheduled for 11 am. 
#   Otherwise the Optimal flight is the day before the meeting and there should be a reasonable delay of 2 hours between arrival at hotel, and start of meeting.
#   
#   HOTEL BOOKINGS: All hotel bookings should be made on the basis that checking will take place between the hours of 7am and 10.30 pm, i.e. if a taxi from the airport will only arrive at the airport before 7am, then this will need manual approval. 
#   If the taxi will only arrive at the hotel after 11pm, then this will also need human approval. 
#   The hotel location should be a maximum of 25 minutes by taxi from the meeting venue, although if possible, it would be best to choose a hotel within a 10 minute walking distance from the venue, approximately a kilometer at the maximum walking distance.
#   TAXI BOOKING: The taxi should be for the trip from the airport to the hotel, BUT not back again, this can be booked locally


# https://www.geeksforgeeks.org/python/introduction-to-fastapi/


""" 
    export OLLAMA_BASE_URL='http://localhost:11434/v1'
    export OLLAMA_API_KEY='123456'
    
 """
import asyncio
from pydantic import BaseModel, Field # used for data validation and data parsing
from datetime import date
from typing import Union, Optional, Literal
from pydantic_ai import Agent, RunContext
from dataclasses import dataclass
import httpx
from fastapi import FastAPI, Header, HTTPException, BackgroundTasks, Depends
from datetime import time
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

import logfire

# need a really flat structure otherwise the model struggles with the final output
class TripDetails(BaseModel):
    
    destination_city: str = Field(description="The city where the meeting/event takes place.")
    meeting_date: date = Field(description="The date of the actual meeting.")
    arrival_date: date = Field(description="The usually a day before the actual meeting.")
    departure_date: date = Field(description="The usually a same day as the actual meeting.")
    
    flight_out_date: date = Field(None, description="Flight departure date.")
    flight_out_time: time = Field(description="The departure time .")
    flight_out_airport: str = Field(description="The departure airport.")
    flight_out_flightNo: str = Field(description="Either the departure flight booking ref, or the actual flight no if known")
    
    flight_return_date: date = Field(None, description="Flight return date.")
    flight_return_time: time = Field(description="The return time .")
    flight_return_airport: str = Field(description="The return airport.")
    flight_return_flightNo: str = Field(description="Either the return flight booking ref, or the actual flight no if known")
    
    flight_booked: bool = False
    hotel_booked: bool = False
    taxi_booked: bool = False
    
    # Failure fields if something goes wrong
    is_failed: bool = Field(
        default=False, 
        description="Set to True ONLY if a tool failed or an email was sent."
    )
    error_message: Optional[str] = Field(
        default=None, 
        description="The reason why the booking failed, or details of the sent email."
    )
    
AgentOutputType = TripDetails

class CalendarChangeAnalysis(BaseModel):
    action_required: Literal['rebook', 'cancel', 'ignore']
    extracted_city: Optional[str] = None
    extracted_date: Optional[str] = None
    change_summary: str
    

# START HERE, I DON'T THINK THAT THE LOGIC BELOW IS HELPFUL, IS PROBABLY CONFUSING
class MockTravelEngine:
    """A local class simulating live flight/hotel databases."""
    
    def search_and_book_flight(self, city: str, target_date: date) -> str:
        # Business logic: Figure out the nearest airport automatically
        airport = city #"LHR (London Heathrow)" if "london" in city.lower() else "GENERIC-AIRPORT"
        return f"Confirmed: Flight to {airport} on {target_date}. Confirmation Flight Number: #FL-9938"

    def search_and_book_hotel(self, city: str, check_in: date, check_out: date) -> str:
        # Business logic: Figure out the check in and check out dates automatically
        return f"Confirmed: Stay in {city} from {check_in} to {check_out}. Confirmation Hotel Booking: #HT-1102"
    
    def search_and_book_taxi(self, destination_city: str, arrival_date: str, departure_date: str) -> str:
        return f"Taxi booked for {arrival_date}, we will monitor your flight details and pick up up within 15 minutes of your landing"
    

class MockEmailClient:
    """A local class simulating an Email Client."""
    
    async def send(self, to_email: str, body: str) -> bool:
        """Simulates sending an email by printing it out to the terminal logs."""
        
        print(f"\n[MOCK EMAIL SENT TO: {to_email}]")
        print(body)
        print("-" * 40)
        return True
    
# This dictionary simulates a real calendar event on Google
mock_google_calendar_database = {
    "event_id_123": {
        "summary": "Project Meeting",
        "description": "Initial text: Meeting in London on Nov 30."
    }
}

# Define the type configuration for your dependencies
# class AgentDependencies:
#     def __init__(self, travel_engine: MockTravelEngine):
#         self.travel = travel_engine
@dataclass # this decorator will make Python write the necessary Class constructor code, where the self.xyz will be completed for each of the two values
class AgentDependencies:
    travel_engine: str
    email_service: str
        


# 1. Configure logfire to ONLY output to the terminal console
logfire.configure(send_to_logfire='never')

# 2. Instrument Pydantic AI
logfire.instrument_pydantic_ai()


# 1. User manually changes description to Paris (Triggers first run)
mock_google_calendar_database["event_id_123"]["description"] = (
    "Hey assistant, change of plans. It is now in Paris on 2026-12-01 instead."
)

myTravelEngine = MockTravelEngine()
myEmailClient = MockEmailClient()

deps = AgentDependencies(travel_engine=myTravelEngine, email_service=myEmailClient)

# =====================================================================
# AGENT DEFINITIONS
# =====================================================================

agent = Agent(
    'ollama:gpt-oss:20b',
    deps_type=deps,
    output_type=AgentOutputType, # this is only type checking, no passing of context happens here
    system_prompt=(
        "You are a travel booking assistant. Your job is to book travel for a given meeting date.\n\n"
        "CRITICAL RULES:\n"
        "1. The company office is based in London. Choose flights from London Heathrow (long haul), "
        "London City (short flights), or London Gatwick (all others).\n"
        "2. If you encounter any critical failures while executing 'search_and_book', you must immediately "
        "call 'send_clarification_email' to notify 'fred@bigcompany.com'.\n"
        "3. Once 'send_clarification_email' has been executed, you must IMMEDIATELY stop calling tools and "
        "return a 'BookingFailure' object. Do not attempt to book anything else."
    ),
    retries=3
)


calendar_webhook_agent = Agent(
    'ollama:qwen3.5:9B',
    output_type=CalendarChangeAnalysis,
    system_prompt=(
        "You analyze calendar updates. Compare the text against context. "
        "CRITICAL: If the description contains text showing that flights/hotels are ALREADY booked "
        "or says 'AUTO-BOOKED BY ASSISTANT', you must set 'action_required' to 'ignore' to prevent loops."
            ),
    retries=2
    )

# 1. Tool for communicating failures back to the user
@agent.tool
async def send_clarification_email(
    ctx: RunContext[AgentDependencies], 
    recipient: str, 
    message: str
) -> str:
    """Sends an email to the user when details are missing or an error occurs."""
    email_body = (
        f"Hi there,\n\n"
        f"I tried processing your calendar booking request, but ran into an issue:\n"
        f"{message}\n\n"
        f"Please update your calendar entry with further instructions (e.g., change dates, "
        f"adjust budget, or change location) so I can try again!\n\n"
        f"Best,\nYour AI Travel Assistant"
    )

    # In production, use your injected email client to dispatch the message
    # await ctx.deps.email_service.send(to=user_email, body=email_body)
    print(f"Outbound email sent to {recipient}:\n{email_body}")
    return "SUCCESS: Email sent. Stop processing immediately and return a BookingFailure state."

# Register a tool that the Agent can call when it's ready to execute
# The TripDetails parameter below is passed to the agent as a standard JSON schema.
@agent.tool
#def search_and_book(ctx: RunContext[AgentDependencies], trip: TripDetails) -> str: The TripDetails object seems to be confusing the agent
def search_and_book(ctx: RunContext[AgentDependencies], destination_city: str, meeting_date: str, arrival_date: str, departure_date: str) -> str:
    """Executes a live booking for a trip.

        ONLY call this tool if you have explicitly extracted the destination city and meeting date requirements.
        Do NOT call this tool multiple times for the same event.

        Args:
            destination_city: The name of the target destination city (e.g., 'Paris').
            meeting_date: The date of the meeting in ISO format (YYYY-MM-DD).
            arrival_date: The date of arrival in ISO format (YYYY-MM-DD).
            departure_date: The date of departure in ISO format (YYYY-MM-DD).
        """
    try:
        # Business logic validation before execution
        if not destination_city or not meeting_date:
            return "CRITICAL FAILURE: Missing required destination_city or meeting_date fields."

        # Execute booking components
        arrival = ctx.deps.travel_engine.search_and_book_flight(destination_city, meeting_date)
        hotel = ctx.deps.travel_engine.search_and_book_hotel(destination_city, arrival_date, departure_date)
        taxi = ctx.deps.travel_engine.search_and_book_taxi(destination_city, arrival_date, departure_date)
        
        return f"{arrival} | {hotel} | Taxi: {taxi}"
    
    except Exception as e:
        # Return the error text to Agent.
        # The error reason tells the model why it failed so it can use that info for the email.
        return f"CRITICAL FAILURE: Could not complete booking. Reason from system: {str(e)}"
               


async def handle_incoming_webhook(event_id: str):
    
    try:
        # Fetch current calendar state
        event = mock_google_calendar_database[event_id]
        current_text = event["description"]
        
        print(f"\n[Webhook Alert] Calendar event '{event_id}' updated!")
        print(f"Current Description Text: \"{current_text}\"")
        
        # Run the Analyzer Agent
        analysis_result = await calendar_webhook_agent.run(f"Analyze: {current_text}")
        analysis = analysis_result.output
        
        print(f"Analysis: Action={analysis.action_required}. Summary: {analysis.change_summary}")
                
        if analysis.action_required == 'rebook':
            print("Triggering Booking Agent...")
            
            result = await agent.run(
                f"The trip details are as follows: the meeting will take place in Paris on the 1st of December 2026. Please search for and book the appropriate trip.", 
                deps=deps # only now does the live context get passed in
            )
            
            print("Booking Agent finished...")        
            # Inspect the result
            structured_data = result.output
            
            print("\n================= AGENT EXECUTION HISTORY =================")

            for index, message in enumerate(result.all_messages(), 1):
                print(f"\n[Step {index}] ", end="")
                
                # 1. Handle incoming requests/instructions from the user or framework
                if isinstance(message, ModelRequest):
                    print("FRAMEWORK / USER REQUEST:")
                    for part in message.parts:
                        if isinstance(part, UserPromptPart):  # If it's a standard string prompt
                            print(f"  Prompt: \"{part.content}\"")
                        elif isinstance(part, ToolReturnPart): # If it's data coming back from a tool
                            print(f"Tool '{part.tool_name}' returned: {part.content}")

                # 2. Handle actions the LLM decided to take
                elif isinstance(message, ModelResponse):
                    print("MODEL RESPONSE:")
                    print(structured_data.model_dump_json(indent=2))
                    
                    for part in message.parts:
                        if isinstance(part, TextPart) and part.content.strip():
                            pass
                                    
                        elif isinstance(part, ToolCallPart): # If the model calls a tool
                            print(f"Called Tool: '{part.tool_name}' with args: {part.args}")

            print("\n===========================================================")
            
                
            # --- SIMULATE THE CALENDAR WRITE-BACK CALLBACK ---
            print("Updating Google Calendar description with confirmation details...")
            
            new_description = (
                f"{current_text}\n\n"
                f"[AUTO-BOOKED BY ASSISTANT]\n"
                f"Status: Confirmed to {analysis.extracted_city} on {analysis.extracted_date}\n"
                f"Flight No: {structured_data.flight_out_flightNo} | Hotel: Booked"
            )
                       
            # Save back to our mock database
            mock_google_calendar_database[event_id]["description"] = new_description
            print("Calendar updated successfully in Google Database.")
            
            # Triggering a simulated secondary webhook to test loop prevention!
            await handle_incoming_webhook(event_id)
            
        else:
            print("Loop Safely Broken: No action taken for this event update.")
            
    except Exception as e:
        print(f"In handle_incoming_webhook, Error Type: {type(e).__name__}")
        print(f"Error Message: {e}")
        
        

async def main():
    
    try:    
        await handle_incoming_webhook("event_id_123")
    
    except Exception as e:
        print(f"In main, Error Type: {type(e).__name__}")
        print(f"Error Message: {e}")
    


if __name__ == "__main__":
    asyncio.run(main())
    
    
    
# # 1. Define the structured output the AI must return
# class ProjectAssessment(BaseModel):
#     project_name: str = Field(description="The name of the AI project")
#     is_feasible: bool = Field(description="Whether the project architecture is sound")
#     risk_score: int = Field(ge=1, le=10, description="Risk score from 1 to 10")
#     reasons: list[str] = Field(description="Key factors supporting the assessment")

# # 2. Define your dependencies container
# @dataclass
# class AgentDependencies:
#     http_client: httpx.AsyncClient
#     vector_db_url: str



# # 4. Inject dependencies dynamically into a tool
# @assessment_agent.tool
# async def check_infrastructure_status(ctx: RunContext[AgentDependencies], service_name: str) -> str:
#     """Checks the health of the internal microservices using the injected HTTP client."""
#     # Pull the injected client from the context safely
#     client = ctx.deps.http_client 
    
#     try:
#         # Example using the injected dependency context
#         response = await client.get(f"{ctx.deps.vector_db_url}/health")
#         if response.status_code == 200:
#             return f"Service '{service_name}' is fully operational."
#         return f"Service '{service_name}' is degraded."
#     except Exception:
#         return f"Could not reach '{service_name}' infrastructure."

# # 5. Execute the agent workflow
# async def main():
#     # Dependencies are instantiated outside the agent, making them easy to mock in tests!
#     async with httpx.AsyncClient() as client:
#         deps = AgentDependencies(
#             http_client=client,
#             vector_db_url="https://vectordb.local"
#         )
        
#         # Run the agent and pass the dependencies
#         result = await assessment_agent.run(
#             "Evaluate our new dependency injection AI project plan.",
#             deps=deps
#         )
        
#         # The result is fully typed and validated as a ProjectAssessment instance
#         print(f"Feasible: {result.data.is_feasible}")
#         print(f"Risk Score: {result.data.risk_score}")
        
        
       
        

# app = FastAPI(title="AI Calendar Automation Service")

# # 1. Setup Dependency Container
# @dataclass
# class AgentDeps:
#     http_client: httpx.AsyncClient

# # 2. Define the Pydantic AI Agent
# calendar_agent = Agent(
#     'openai:gpt-4o',
#     deps_type=AgentDeps,
#     system_prompt="Analyze the calendar event. If it explicitly or implicitly requires a booking, flag it."
# )

# # 3. Initialize the Agent with your specific models and dependency types
# assessment_agent = Agent(
#     'openai:gpt-4o',  # Or any supported model provider
#     deps_type=AgentDependencies,
#     result_type=ProjectAssessment,
#     system_prompt="You are an expert AI solution architect. Assess the project based on provided telemetry."
# )

# # Initialize the PydanticAI Agent
# travel_agent = Agent(
#     'ollama:qwen2.5:7b', # Perfect small local model for structured tool tasks
#     deps_type=AgentDependencies,
#     result_type=TripDetails, # Forces the agent to output the structured model
#     system_prompt="You are a precise corporate travel assistant. Help the user plan their trip details."
# )

# # Shared HTTPX client lifecycle manager for FastAPI
# async def get_http_client():
#     async with httpx.AsyncClient() as client:
#         yield client

# # 3. The Background Worker Process
# async def run_calendar_agent_workflow(resource_id: str, client: httpx.AsyncClient):
#     """
#     This runs asynchronously in the background. FastAPI has already 
#     responded to the calendar provider, so the agent can take its time.
#     """
#     # Step A: Fetch the actual event data using the resource_id sent by the webhook
#     # (In production, you'd fetch OAuth tokens and call Google/Outlook API here)
#     print(f"🔄 Webhook triggered background sync for resource: {resource_id}")
    
#     # Step B: Run the Pydantic AI Agent with injected dependencies
#     deps = AgentDeps(http_client=client)
    
#     # In a real app, you would pass the fetched calendar event text/data here
#     response = await calendar_agent.run(
#         "User calendar update received. Check for booking requirements.", 
#         deps=deps
#     )
    
#     print(f"🤖 Agent Execution Finished. Result: {response.data}")

# # 4. The FastAPI Webhook Receiver Endpoint
# @app.post("/webhooks/calendar")
# async def handle_calendar_webhook(
#     background_tasks: BackgroundTasks,
#     # Providers send specific headers you can use to verify requests or track changes
#     x_goog_resource_id: str = Header(None, alias="X-Goog-Resource-ID"),
#     x_goog_changed: str = Header(None, alias="X-Goog-Changed"),
#     client: httpx.AsyncClient = Depends(get_http_client)
# ):
#     """
#     Endpoint exposed to the internet. Google/Microsoft hit this URL 
#     whenever the user's calendar changes.
#     """
#     # Secure your webhook: validate that it's actually coming from your provider
#     if not x_goog_resource_id:
#         raise HTTPException(status_code=400, detail="Missing sync headers.")

#     # Push the heavy AI processing to a background thread execution loop
#     background_tasks.add_task(
#         run_calendar_agent_workflow, 
#         resource_id=x_goog_resource_id, 
#         client=client
#     )

# Return an immediate 200/202 status code so the calendar provider knows you received it
# return {"status": "acknowledged", "resource_id": x_goog_resource_id}


# class BookingTrigger(BaseModel):
#     event_id: str
#     summary: str
#     start_time: datetime
#     booking_target: str = Field(
#         description="Extracted company, person, or service type to book (e.g., 'Dentist', 'Hertz Car Rental')"
#     )
#     urgency: str = Field(description="High, Medium, or Low based on how soon the appointment is")

# @calendar_agent.tool
# async def scan_upcoming_events(ctx: RunContext[CalendarDeps], days_ahead: int = 7) -> list[dict]:
#     """Retrieves upcoming calendar events using the injected authenticated API client."""
#     # ctx.deps.calendar_client handles OAuth and token renewals automatically
#     events = await ctx.deps.calendar_client.get_upcoming_events(days_ahead)
#     return events