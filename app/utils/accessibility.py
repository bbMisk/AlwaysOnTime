import os
import requests
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Public MTA Elevator/Escalator Status Endpoint (No key required, but needs User-Agent)
ELEVATOR_STATUS_URL = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fnyct_ene.json"

def fetch_elevator_status():
    """
    Fetch real-time elevator and escalator status from MTA API.
    Returns a dictionary mapping station names/IDs to outage information.
    """
    # MTA API blocks requests without a standard User-Agent
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(ELEVATOR_STATUS_URL, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        outages = {}
        
        for item in data:
            if item.get('isupcomingoutage') == 'Y':
                continue # Skip upcoming outages, focus on current
                
            station_name = item.get('station')
            if not station_name:
                continue
                
            # Normalize station name if needed
            station_name = station_name.strip()
            
            if station_name not in outages:
                outages[station_name] = []
                
            outages[station_name].append({
                "equipment": item.get('equipment'),
                "type": item.get('equipmenttype', 'Unknown'),
                "serving": item.get('serving', 'Unknown'),
                "reason": item.get('reason', 'Unknown'),
                "estimated_return": item.get('estimatedreturntoservice', 'Unknown'),
                "outage_type": "Maintenance" if item.get('ismaintenanceoutage') == 'Y' else "Unplanned"
            })
            
        return outages

    except Exception as e:
        logger.error(f"Error fetching elevator status: {e}")
        return {}
