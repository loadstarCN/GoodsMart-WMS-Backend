import pytest
from warehouse.picking.route_geometry import ordered_walk, pick_points, RouteGeometryError


def shape(ident, kind, x, y, width, height, **extra):
    return dict(id=ident,type=kind,name=ident,x=x,y=y,width=width,height=height,rotation=0,**extra)


def floor(ident, objects):
    return dict(id=ident,name=ident,width=20,height=20,objects=objects)


def test_walking_route_goes_around_wall_and_returns_to_start():
    layout = {'floors':[floor('f',[shape('wall','wall',9,3,2,14)])]}
    result = ordered_walk(layout,('f',2,10),[dict(id=1,points=[('f',18,10)])])
    assert result['unreachable'] == [] and result['returning'] is not None
    assert result['distance'] > 32
    assert any(y < 3 or y > 17 for _,x,y in result['legs'][0]['path'])
    assert result['transfers'] == 0


def test_wall_across_floor_is_unreachable_not_a_boundary_shortcut():
    layout = {'floors':[floor('f',[shape('wall','wall',9,0,2,20)])]}
    result = ordered_walk(layout,('f',2,10),[dict(id=1,points=[('f',18,10)])])
    assert result['unreachable'] == [1] and result['legs'] == []


def test_door_opens_only_the_overlapping_wall_not_a_rack():
    wall=shape('wall','wall',9,0,2,20)
    door=shape('door','door',9,8,2,4)
    layout={'floors':[floor('f',[wall,door])]}
    result=ordered_walk(layout,('f',2,10),[dict(id=1,points=[('f',18,10)])])
    assert result['unreachable']==[] and result['distance']==pytest.approx(32)
    wall['type']='rack'
    assert ordered_walk(layout,('f',2,10),[dict(id=1,points=[('f',18,10)])])['unreachable']==[1]


def test_connector_travel_uses_only_explicit_links_and_accounts_for_time():
    layout = {'floors':[
        floor('one',[shape('a','connector',4,4,2,2,target_id='b',travel_seconds=45)]),
        floor('two',[shape('b','connector',4,4,2,2,target_id='a',travel_seconds=45)])]}
    result = ordered_walk(layout,('one',1,5),[dict(id=1,points=[('two',9,5)])])
    assert result['unreachable'] == [] and result['transfers'] == 2
    assert result['distance'] == pytest.approx(16)
    assert result['seconds'] == pytest.approx(16/1.2+90)
    del layout['floors'][1]['objects'][0]['target_id']
    one_way = ordered_walk(layout,('one',1,5),[dict(id=1,points=[('two',9,5)])])
    assert one_way['returning'] is None


def test_pick_faces_rotate_and_nearest_stop_is_selected_first():
    rack = shape('rack','rack',9,8,2,4,pick_face='both')
    assert pick_points(rack) == [(10,12.15),(10,7.85)]
    rack['rotation']=90
    assert pick_points(rack) == [(11.15,10),(8.85,10)]
    layout={'floors':[floor('f',[rack])]}
    result=ordered_walk(layout,('f',1,10),[dict(id=2,points=[('f',18,10)]),dict(id=1,points=[('f',*p) for p in pick_points(rack)])])
    assert [leg['stop_id'] for leg in result['legs']] == [1,2]
    with pytest.raises(RouteGeometryError,match='start_blocked'):
        ordered_walk(layout,('f',10,10),[])


def test_resume_from_last_pick_returns_to_original_station():
    layout={'floors':[floor('f',[])]}
    result=ordered_walk(layout,('f',8,1),[dict(id=1,points=[('f',10,1)])],return_point=('f',1,1))
    assert result['distance']==11
    assert result['returning']['path'][-1]==('f',1,1)
    last=ordered_walk(layout,('f',10,1),[],return_point=('f',1,1))
    assert last['distance']==9 and last['returning'] is not None
