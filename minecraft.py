from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
import socket, threading, pickle, struct

# ------------------- NETWORK CONFIG -------------------
SERVER = '192.168.1.138'  # Replace with your server IP
PORT = 12345

client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_socket.connect((SERVER, PORT))

CHUNK_BLOCKS = {}
PLAYERS = {}
DIRTY_CHUNKS = set()
world_loaded = False  # Wait for first server state

# ------------------- NETWORK FUNCTIONS -------------------
def send_msg(msg):
    data = pickle.dumps(msg)
    length = struct.pack('!I', len(data))
    client_socket.sendall(length + data)

def recvall(sock, n):
    data = b''
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data

def recv_msg():
    raw_len = recvall(client_socket, 4)
    if not raw_len: return None
    msg_len = struct.unpack('!I', raw_len)[0]
    return pickle.loads(recvall(client_socket, msg_len))

def network_listener():
    global CHUNK_BLOCKS, PLAYERS, world_loaded, DIRTY_CHUNKS
    while True:
        state = recv_msg()
        if state:
            old_chunks = set(CHUNK_BLOCKS.keys())
            CHUNK_BLOCKS = state.get('CHUNK_BLOCKS', {})
            PLAYERS = state.get('PLAYERS', {})

            # Mark all changed chunks for rebuild
            new_chunks = set(CHUNK_BLOCKS.keys())
            changed_chunks = new_chunks.union(old_chunks)
            DIRTY_CHUNKS.update(changed_chunks)

            if not world_loaded:
                print("[WORLD LOADED]")
                world_loaded = True

threading.Thread(target=network_listener, daemon=True).start()

# ------------------- URSINA SETUP -------------------
app = Ursina()
CHUNKS = {}
BLOCK_COLLIDERS = {}
CHUNK_SIZE = 16
RENDER_DISTANCE = 3
INTERACTION_DISTANCE = 5

grass_texture = load_texture('grass.png')

# ------------------- CUBE DATA -------------------
cube_vertices = [
    Vec3(-0.5,-0.5,-0.5), Vec3(0.5,-0.5,-0.5), Vec3(0.5,0.5,-0.5), Vec3(-0.5,0.5,-0.5),
    Vec3(0.5,-0.5,0.5), Vec3(-0.5,-0.5,0.5), Vec3(-0.5,0.5,0.5), Vec3(0.5,0.5,0.5),
    Vec3(-0.5,-0.5,0.5), Vec3(-0.5,-0.5,-0.5), Vec3(-0.5,0.5,-0.5), Vec3(-0.5,0.5,0.5),
    Vec3(0.5,-0.5,-0.5), Vec3(0.5,-0.5,0.5), Vec3(0.5,0.5,0.5), Vec3(0.5,0.5,-0.5),
    Vec3(-0.5,0.5,-0.5), Vec3(0.5,0.5,-0.5), Vec3(0.5,0.5,0.5), Vec3(-0.5,0.5,0.5),
    Vec3(-0.5,-0.5,0.5), Vec3(0.5,-0.5,0.5), Vec3(0.5,-0.5,-0.5), Vec3(-0.5,-0.5,-0.5),
]

cube_triangles = [
    0,1,2,2,3,0,4,5,6,6,7,4,8,9,10,10,11,8,
    12,13,14,14,15,12,16,17,18,18,19,16,20,21,22,22,23,20
]

face_uvs = [Vec2(0,0), Vec2(1,0), Vec2(1,1), Vec2(0,1)]
cube_uvs = []
for _ in range(6):
    cube_uvs += face_uvs

# ------------------- HELPER FUNCTIONS -------------------
def get_chunk(pos):
    x,y,z = pos
    return (x // CHUNK_SIZE, z // CHUNK_SIZE)

def build_chunk_mesh(chunk_x, chunk_z):
    if (chunk_x, chunk_z) not in CHUNK_BLOCKS: return
    vertices, triangles, uvs = [], [], []
    offset = 0
    for block in CHUNK_BLOCKS[(chunk_x, chunk_z)]:
        bx,by,bz = block
        for v in cube_vertices:
            vertices.append(Vec3(v.x+bx, v.y+by, v.z+bz))
        for t in cube_triangles:
            triangles.append(t+offset)
        for uv in cube_uvs:
            uvs.append(uv)
        offset += 24
    mesh = Mesh(vertices=vertices, triangles=triangles, uvs=uvs, mode='triangle')
    if (chunk_x,chunk_z) in CHUNKS:
        CHUNKS[(chunk_x,chunk_z)].model = mesh
    else:
        CHUNKS[(chunk_x,chunk_z)] = Entity(model=mesh, texture=grass_texture)

def update_chunk_meshes():
    if not world_loaded: return
    player_chunk = get_chunk((player.x, player.y, player.z))

    # rebuild dirty chunks
    for chunk in list(DIRTY_CHUNKS):
        if chunk in CHUNK_BLOCKS:
            build_chunk_mesh(*chunk)
        DIRTY_CHUNKS.remove(chunk)

    # render nearby chunks
    for dx in range(-RENDER_DISTANCE, RENDER_DISTANCE+1):
        for dz in range(-RENDER_DISTANCE, RENDER_DISTANCE+1):
            chunk = (player_chunk[0]+dx, player_chunk[1]+dz)
            if chunk in CHUNK_BLOCKS and chunk not in CHUNKS:
                build_chunk_mesh(*chunk)

    # destroy far chunks
    for chunk in list(CHUNKS.keys()):
        if chunk not in [(player_chunk[0]+dx, player_chunk[1]+dz)
                         for dx in range(-RENDER_DISTANCE, RENDER_DISTANCE+1)
                         for dz in range(-RENDER_DISTANCE, RENDER_DISTANCE+1)]:
            destroy(CHUNKS[chunk])
            del CHUNKS[chunk]

def update_block_colliders():
    if not world_loaded: return
    player_pos = Vec3(round(player.x), 0, round(player.z))
    for chunk in CHUNKS:
        for pos in CHUNK_BLOCKS.get(chunk, []):
            if pos not in BLOCK_COLLIDERS and (Vec3(*pos) - player_pos).length() <= INTERACTION_DISTANCE:
                BLOCK_COLLIDERS[pos] = Entity(
                    position=pos,
                    model='cube',
                    collider='box',
                    visible=False
                )
    for pos in list(BLOCK_COLLIDERS.keys()):
        if (Vec3(*pos) - player_pos).length() > INTERACTION_DISTANCE:
            destroy(BLOCK_COLLIDERS[pos])
            del BLOCK_COLLIDERS[pos]

# ------------------- INPUT -------------------
def input(key):
    if not world_loaded: return
    if key == 'left mouse down' or key == 'right mouse down':
        hit = mouse.hovered_entity
        if hit:
            pos = tuple(round(v) for v in hit.position)
            chunk = get_chunk(pos)
            if key == 'left mouse down':
                send_msg({'type':'place_block','position':tuple(int(pos[i]+mouse.normal[i]) for i in range(3)),'chunk':chunk})
            elif key == 'right mouse down':
                send_msg({'type':'remove_block','position':pos,'chunk':chunk})

# ------------------- PLAYER -------------------
player = FirstPersonController(y=10)
player_body = Entity(
    parent=player,
    model='cube',
    color=color.azure,
    scale=Vec3(0.5,1,0.3),
    position=Vec3(0,-0.5,0),
    collider=None
)

# ------------------- MAIN UPDATE -------------------
def update():
    if not world_loaded: return
    send_msg({'type':'update_pos','position':(player.x,player.y,player.z)})
    update_chunk_meshes()
    update_block_colliders()

app.run()
