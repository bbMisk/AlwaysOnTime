
import networkx as nx
from app.models import Station
import logging

logger = logging.getLogger(__name__)

# Singleton graph instance
_subway_graph = None

def build_graph():
    """
    Build the subway network graph from the database and hardcoded logic.
    Nodes: Station IDs
    Edges: 
        - Adjacent stations on same line (weight: ~2 min)
        - Transfers at same station (weight: ~4 min)
    """
    global _subway_graph
    if _subway_graph:
        return _subway_graph
    
    logger.info("Building subway graph...")
    G = nx.Graph()
    
    # 1. Add all stations as nodes
    all_stations = Station.get_all()
    logger.info(f"Adding {len(all_stations)} stations to graph")
    station_map = {s['id']: s for s in all_stations}
    
    for s in all_stations:
        G.add_node(s['id'], name=s['name'], lines=s['lines'], lat=s['latitude'], lon=s['longitude'])
    
    # 2. Add edges from line sequences
    # Fetch line sequences from DB
    from app.db import get_db
    import json
    
    db = get_db()
    sequences = db.execute('SELECT * FROM line_sequences').fetchall()
    logger.info(f"Processing {len(sequences)} line sequences")
    
    for row in sequences:
        line_id = row['line_id']
        try:
            sequence = json.loads(row['sequence_json'])
        except json.JSONDecodeError:
            logger.error(f"Failed to decode sequence for line {line_id}")
            continue
            
        # Add edges between adjacent stations
        for i in range(len(sequence) - 1):
            u = sequence[i]
            v = sequence[i+1]
            
            if u in station_map and v in station_map:
                # Add edge with line attribute
                # Weight: ~2 minutes between stations (heuristic)
                # We use a MultiGraph or just store list of lines on the edge
                if G.has_edge(u, v):
                    G[u][v]['lines'].append(line_id)
                else:
                    G.add_edge(u, v, weight=2, lines=[line_id])
    
    return G

def find_path(origin_id, dest_id):
    """
    Find the shortest path between two stations.
    Returns a list of segments:
    [
        {
            "from_id": "101",
            "to_id": "103",
            "lines": ["1"],
            "stations": ["101", "102", "103"]
        },
        ...
    ]
    """
    G = get_graph()
    
    try:
        # Use Dijkstra to find shortest path based on weight (minutes)
        path = nx.shortest_path(G, origin_id, dest_id, weight='weight')
        
        # Convert node path to segments (group by line)
        segments = []
        if not path or len(path) < 2:
            return []
            
        current_segment = {
            "from_id": path[0],
            "stations": [path[0]],
            "lines": set(G[path[0]][path[1]]['lines'])
        }
        
        for i in range(1, len(path) - 1):
            u = path[i]
            v = path[i+1]
            
            # Check connectivity to next node
            next_lines = set(G[u][v]['lines'])
            
            # Find common lines between current segment and next edge
            common_lines = current_segment['lines'].intersection(next_lines)
            
            if common_lines:
                # Continue on same line(s)
                current_segment['stations'].append(u)
                current_segment['lines'] = common_lines # Narrow down to lines that go all the way
            else:
                # Transfer needed!
                current_segment['stations'].append(u)
                current_segment['to_id'] = u
                # Pick a representative line (e.g., first one sorted)
                current_segment['line'] = sorted(list(current_segment['lines']))[0]
                del current_segment['lines'] # Cleanup
                segments.append(current_segment)
                
                # Start new segment
                current_segment = {
                    "from_id": u,
                    "stations": [u],
                    "lines": next_lines
                }
        
        # Finish last segment
        current_segment['stations'].append(path[-1])
        current_segment['to_id'] = path[-1]
        current_segment['line'] = sorted(list(current_segment['lines']))[0]
        del current_segment['lines']
        segments.append(current_segment)
        
        return segments
        
    except nx.NetworkXNoPath:
        return None
    except Exception as e:
        logger.error(f"Path finding error: {e}")
        return None

def get_graph():
    global _subway_graph
    if not _subway_graph:
        _subway_graph = build_graph()
    return _subway_graph
