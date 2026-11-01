"""Rectilinear walking paths on a published layout, never through racks or walls.

Coordinates are metres. Connector links are directed exactly as configured.
The route order is a nearest-next-stop heuristic, not a claim of global optimality.
"""
from bisect import bisect_left
from functools import lru_cache
import heapq
import math

WALK_METRES_PER_SECOND = 1.2
CLEARANCE = 0.15
MAX_GRID_NODES = 120_000


class RouteGeometryError(ValueError):
    pass


def pick_points(rack):
    x, y, w, h = (rack[key] for key in ('x', 'y', 'width', 'height'))
    faces = [(x+w/2, y+h+CLEARANCE), (x+w+CLEARANCE, y+h/2),
             (x+w/2, y-CLEARANCE), (x-CLEARANCE, y+h/2)]
    front = int(rack.get('rotation', 0)//90)
    face = rack.get('pick_face', 'front')
    return [faces[index % 4] for index in ([front, front+2] if face == 'both' else [front+2] if face == 'back' else [front])]


def obstacle_parts(obj, doors):
    parts=[(obj['x'],obj['y'],obj['x']+obj['width'],obj['y']+obj['height'])]
    if obj['type'] != 'wall':
        return parts
    for door in doors:
        remainder=[]
        for left,top,right,bottom in parts:
            x1,y1=max(left,door['x']),max(top,door['y'])
            x2,y2=min(right,door['x']+door['width']),min(bottom,door['y']+door['height'])
            if x1>=x2 or y1>=y2:
                remainder.append((left,top,right,bottom))
                continue
            for part in ((left,top,x1,bottom),(x2,top,right,bottom),(x1,top,x2,y1),(x1,y2,x2,bottom)):
                if part[0]<part[2] and part[1]<part[3]:
                    remainder.append(part)
        parts=remainder
    return parts


class WalkingGraph:
    def __init__(self, layout, points):
        self.floors, self.links = {}, {}
        self.expansions = 0
        connectors = {}
        node_count = 0
        for floor in layout['floors']:
            fid = floor['id']
            xs, ys = {0.0, float(floor['width'])}, {0.0, float(floor['height'])}
            obstacles = []
            doors=[obj for obj in floor['objects'] if obj['type']=='door']
            for obj in floor['objects']:
                if obj['type'] in ('rack', 'wall'):
                    for left,top,right,bottom in obstacle_parts(obj,doors):
                        # Extend past touching boundaries to prevent an invented
                        # zero-width passage along the edge of the floor.
                        rect=(left-CLEARANCE,top-CLEARANCE,right+CLEARANCE,bottom+CLEARANCE)
                        obstacles.append(rect)
                        xs.update(value for value in (rect[0],rect[2]) if 0 <= value <= floor['width'])
                        ys.update(value for value in (rect[1],rect[3]) if 0 <= value <= floor['height'])
                if obj['type'] == 'connector':
                    point = (fid, obj['x']+obj['width']/2, obj['y']+obj['height']/2)
                    connectors[obj['id']] = (point, obj)
                    xs.add(point[1]); ys.add(point[2])
            for point in points:
                if point[0] == fid and 0 <= point[1] <= floor['width'] and 0 <= point[2] <= floor['height']:
                    xs.add(point[1]); ys.add(point[2])
            xs, ys = sorted(xs), sorted(ys)
            node_count += len(xs)*len(ys)
            if node_count > MAX_GRID_NODES:
                raise RouteGeometryError('layout_too_complex')
            self.floors[fid] = dict(xs=xs, ys=ys, obstacles=obstacles)
        for point, obj in connectors.values():
            target = connectors.get(obj.get('target_id'))
            if target:
                start, end = self.node(point), self.node(target[0])
                if start is not None and end is not None:
                    self.links.setdefault(start, []).append((end, float(obj.get('travel_seconds',0))))

    def node(self, point):
        floor = self.floors.get(point[0])
        if floor is None:
            return None
        x, y = bisect_left(floor['xs'],point[1]), bisect_left(floor['ys'],point[2])
        if x == len(floor['xs']) or y == len(floor['ys']) or floor['xs'][x] != point[1] or floor['ys'][y] != point[2]:
            return None
        if any(left < point[1] < right and top < point[2] < bottom for left,top,right,bottom in floor['obstacles']):
            return None
        return point[0], x, y

    def point(self, node):
        floor = self.floors[node[0]]
        return node[0], floor['xs'][node[1]], floor['ys'][node[2]]

    @lru_cache(maxsize=250_000)
    def clear_edge(self, first, second):
        _, x1, y1 = self.point(first)
        _, x2, y2 = self.point(second)
        for left,top,right,bottom in self.floors[first[0]]['obstacles']:
            if y1 == y2 and top < y1 < bottom and max(min(x1,x2),left) < min(max(x1,x2),right):
                return False
            if x1 == x2 and left < x1 < right and max(min(y1,y2),top) < min(max(y1,y2),bottom):
                return False
        return True

    def neighbours(self, node):
        fid, x, y = node
        floor = self.floors[fid]
        for nx, ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            if 0 <= nx < len(floor['xs']) and 0 <= ny < len(floor['ys']):
                other = (fid,nx,ny)
                if self.clear_edge(node,other):
                    distance = abs(floor['xs'][nx]-floor['xs'][x])+abs(floor['ys'][ny]-floor['ys'][y])
                    yield other, distance/WALK_METRES_PER_SECOND, distance, False
        for other, seconds in self.links.get(node,[]):
            yield other, seconds, 0.0, True

    def shortest(self, start, targets):
        if start is None or not targets:
            return None
        queue, costs, parents = [(0.0,start)], {start:0.0}, {}
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != costs[node]:
                continue
            self.expansions += 1
            if self.expansions > 500_000:
                raise RouteGeometryError('layout_too_complex')
            if node in targets:
                path, distance, transfers, connector_seconds = [node], 0.0, 0, 0.0
                current = node
                while current != start:
                    previous, edge_distance, connector, seconds = parents[current]
                    distance += edge_distance
                    transfers += int(connector)
                    if connector:
                        connector_seconds += seconds
                    path.append(previous); current = previous
                return dict(node=node, seconds=cost, distance=distance, transfers=transfers,
                            connector_seconds=connector_seconds, path=[self.point(value) for value in reversed(path)])
            for other, seconds, distance, connector in self.neighbours(node):
                candidate = cost+seconds
                if candidate < costs.get(other,math.inf):
                    costs[other] = candidate
                    parents[other] = (node,distance,connector,seconds)
                    heapq.heappush(queue,(candidate,other))
        return None

    def close(self):
        # lru_cache on a method retains self; release request-scoped graphs.
        self.clear_edge.cache_clear()


def ordered_walk(layout, start, stops, return_point=None):
    return_point=return_point or start
    points = [start,return_point]+[point for stop in stops for point in stop['points']]
    graph = WalkingGraph(layout,points)
    try:
        origin = graph.node(start)
        if origin is None:
            raise RouteGeometryError('start_blocked')
        destination=graph.node(return_point)
        if destination is None:
            raise RouteGeometryError('start_blocked')
        pending = {stop['id']:{node for point in stop['points'] if (node:=graph.node(point)) is not None} for stop in stops}
        legs, current = [], origin
        while pending:
            result = graph.shortest(current,set().union(*pending.values()))
            if result is None:
                break
            ident = next(key for key,nodes in pending.items() if result['node'] in nodes)
            del pending[ident]
            legs.append(dict(stop_id=ident,**result))
            current = result['node']
        returning = graph.shortest(current,{destination}) if legs or current!=destination else None
        result = dict(legs=legs, unreachable=list(pending), returning=returning, code_order_distance=None,
                    distance=sum(leg['distance'] for leg in legs)+(returning['distance'] if returning else 0),
                    seconds=sum(leg['seconds'] for leg in legs)+(returning['seconds'] if returning else 0),
                    transfers=sum(leg['transfers'] for leg in legs)+(returning['transfers'] if returning else 0))
        if not pending and returning is not None:
            baseline, cursor = [], origin
            try:
                for stop in sorted(stops,key=lambda row:(row.get('location',''),row['id'])):
                    targets = {node for point in stop['points'] if (node:=graph.node(point)) is not None}
                    leg = graph.shortest(cursor,targets)
                    if leg is None:
                        break
                    baseline.append(leg); cursor=leg['node']
                back=graph.shortest(cursor,{destination}) if len(baseline)==len(stops) else None
                if back:
                    result['code_order_distance']=sum(leg['distance'] for leg in baseline)+back['distance']
            except RouteGeometryError:
                pass  # Comparison is optional; retain the successfully computed route.
        return result
    finally:
        graph.close()
