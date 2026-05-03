import sys
import math
import random
from typing import Dict, List, Tuple, Optional, Set
from enum import Enum
from dataclasses import dataclass, field
from collections import deque

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QLabel, QSpinBox, QTextEdit, QGroupBox,
    QGridLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QFrame, QSplitter, QMessageBox
)
from PyQt5.QtCore import Qt, QTimer, QPointF, QRectF, pyqtSignal, QObject
from PyQt5.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont,
    QLinearGradient, QRadialGradient, QCursor
)

# ============================================================================
# DATA STRUCTURES
# ============================================================================

class Operation(Enum):
    """MPLS operations"""
    PUSH = "PUSH"
    SWAP = "SWAP"
    POP = "POP"

@dataclass
class LabelEntry:
    """LFIB entry for a label"""
    incoming_label: Optional[int]
    operation: Operation
    outgoing_label: Optional[int]
    next_hop: Optional[str]

@dataclass
class Router:
    """Router node in the network"""
    id: str
    x: float
    y: float
    lfib: Dict[int, LabelEntry] = field(default_factory=dict)
    routing_table: Dict[str, Tuple[str, int]] = field(default_factory=dict)
    labels_allocated: Set[int] = field(default_factory=set)
    
    def allocate_label(self) -> int:
        """Allocate a unique label"""
        label = random.randint(16, 100)
        while label in self.labels_allocated:
            label = random.randint(16, 100)
        self.labels_allocated.add(label)
        return label

@dataclass
class Link:
    """Link between routers"""
    router1: str
    router2: str
    cost: int = 1

class PacketStatus(Enum):
    """Packet status in simulation"""
    PENDING = "PENDING"
    TRANSITING = "TRANSITING"
    COMPLETED = "COMPLETED"

@dataclass
class Packet:
    """Data packet being forwarded"""
    id: int
    source: str
    destination: str
    current_hop: int = 0
    label_stack: List[int] = field(default_factory=list)
    status: PacketStatus = PacketStatus.PENDING
    progress: float = 0.0
    path: List[str] = field(default_factory=list)
    operation_at_current: Optional[Operation] = None

# ============================================================================
# NETWORK LOGIC ENGINE
# ============================================================================

class MPLSEngine(QObject):
    """Core MPLS logic engine"""
    routers_updated = pyqtSignal()
    lfib_updated = pyqtSignal(str)
    packet_created = pyqtSignal(Packet)
    packet_completed = pyqtSignal(int)
    
    def __init__(self):
        super().__init__()
        self.routers: Dict[str, Router] = {}
        self.links: List[Link] = []
        self.packets: List[Packet] = []
        self.next_packet_id = 0
        self.source = None
        self.destination = None
        
    def add_router(self, router_id: str, x: float, y: float):
        """Add a new router"""
        if router_id not in self.routers:
            self.routers[router_id] = Router(router_id, x, y)
            self.routers_updated.emit()
            
    def remove_router(self, router_id: str):
        """Remove a router"""
        if router_id in self.routers:
            del self.routers[router_id]
            self.links = [l for l in self.links 
                         if l.router1 != router_id and l.router2 != router_id]
            if self.source == router_id:
                self.source = None
            if self.destination == router_id:
                self.destination = None
            self._update_routing_tables()
            self._setup_lsp()
            self.routers_updated.emit()
            
    def add_link(self, router1: str, router2: str, cost: int = 1):
        """Add a link"""
        for link in self.links:
            if (link.router1 == router1 and link.router2 == router2) or \
               (link.router1 == router2 and link.router2 == router1):
                return False
        self.links.append(Link(router1, router2, cost))
        self._update_routing_tables()
        self._setup_lsp()
        return True
        
    def _update_routing_tables(self):
        """Update all routing tables using Dijkstra"""
        for router_id in self.routers:
            distances = {rid: float('inf') for rid in self.routers}
            previous = {rid: None for rid in self.routers}
            distances[router_id] = 0
            unvisited = set(self.routers.keys())
            
            while unvisited:
                current = min(unvisited, key=lambda x: distances[x])
                if distances[current] == float('inf'):
                    break
                unvisited.remove(current)
                
                for link in self.links:
                    neighbor = None
                    if link.router1 == current:
                        neighbor = link.router2
                    elif link.router2 == current:
                        neighbor = link.router1
                    if neighbor and neighbor in unvisited:
                        new_dist = distances[current] + link.cost
                        if new_dist < distances[neighbor]:
                            distances[neighbor] = new_dist
                            previous[neighbor] = current
            
            # Build routing table
            routing_table = {}
            for dest_id in self.routers:
                if dest_id != router_id and distances[dest_id] != float('inf'):
                    # Find next hop
                    current = dest_id
                    while previous[current] != router_id and previous[current] is not None:
                        current = previous[current]
                    if previous[current] == router_id:
                        routing_table[dest_id] = (current, distances[dest_id])
                    elif current != router_id:
                        routing_table[dest_id] = (current, distances[dest_id])
                    
            self.routers[router_id].routing_table = routing_table
            
    def _setup_lsp(self):
        """Setup LSP with labels"""
        if not self.source or not self.destination:
            return
            
        # Clear existing LFIBs
        for router in self.routers.values():
            router.lfib.clear()
            router.labels_allocated.clear()
            
        # Find path
        path = self._get_path(self.source, self.destination)
        if not path or len(path) < 2:
            return
            
        print(f"Setting up LSP on path: {' -> '.join(path)}")
        
        # Setup from egress to ingress
        next_label = None
        
        # Egress: POP
        egress = path[-1]
        if len(path) >= 2:
            # For egress, we create a POP entry
            pop_label = self.routers[egress].allocate_label()
            self.routers[egress].lfib[pop_label] = LabelEntry(
                incoming_label=pop_label,
                operation=Operation.POP,
                outgoing_label=None,
                next_hop=None
            )
            next_label = pop_label
            print(f"{egress}: POP label {pop_label}")
            
            # Intermediate routers: SWAP
            for i in range(len(path) - 2, 0, -1):
                current = path[i]
                next_hop = path[i + 1]
                swap_label = self.routers[current].allocate_label()
                self.routers[current].lfib[swap_label] = LabelEntry(
                    incoming_label=swap_label,
                    operation=Operation.SWAP,
                    outgoing_label=next_label,
                    next_hop=next_hop
                )
                next_label = swap_label
                print(f"{current}: SWAP {swap_label} -> {next_label} via {next_hop}")
            
            # Ingress: PUSH
            ingress = path[0]
            push_label = next_label
            self.routers[ingress].lfib[-1] = LabelEntry(
                incoming_label=None,
                operation=Operation.PUSH,
                outgoing_label=push_label,
                next_hop=path[1]
            )
            print(f"{ingress}: PUSH label {push_label} to {path[1]}")
            
        # Emit updates
        for router in path:
            self.lfib_updated.emit(router)
            
    def _get_path(self, source: str, dest: str) -> List[str]:
        """Find shortest path using Dijkstra"""
        if source not in self.routers or dest not in self.routers:
            return []
            
        distances = {rid: float('inf') for rid in self.routers}
        previous = {rid: None for rid in self.routers}
        distances[source] = 0
        unvisited = set(self.routers.keys())
        
        while unvisited:
            current = min(unvisited, key=lambda x: distances[x])
            if distances[current] == float('inf'):
                break
            unvisited.remove(current)
            
            if current == dest:
                break
                
            for link in self.links:
                neighbor = None
                if link.router1 == current:
                    neighbor = link.router2
                elif link.router2 == current:
                    neighbor = link.router1
                if neighbor and neighbor in unvisited:
                    new_dist = distances[current] + link.cost
                    if new_dist < distances[neighbor]:
                        distances[neighbor] = new_dist
                        previous[neighbor] = current
                        
        # Reconstruct path
        path = []
        current = dest
        while current is not None:
            path.insert(0, current)
            current = previous[current]
            
        return path if len(path) > 1 and path[0] == source else []
        
    def set_source_destination(self, source: str, destination: str):
        """Set source and destination"""
        self.source = source
        self.destination = destination
        if source and destination:
            self._setup_lsp()
        
    def create_packet(self) -> Optional[Packet]:
        """Create a new packet"""
        if not self.source or not self.destination:
            return None
            
        path = self._get_path(self.source, self.destination)
        if not path:
            return None
            
        packet = Packet(
            id=self.next_packet_id,
            source=self.source,
            destination=self.destination,
            path=path,
            current_hop=0,
            status=PacketStatus.PENDING
        )
        self.next_packet_id += 1
        return packet
        
    def get_next_hop_for_packet(self, packet: Packet) -> Tuple[Optional[str], Optional[Operation], Optional[int]]:
        """Get next hop, operation, and label for packet"""
        if packet.current_hop >= len(packet.path) - 1:
            return None, None, None
            
        current_router_id = packet.path[packet.current_hop]
        next_router_id = packet.path[packet.current_hop + 1]
        router = self.routers[current_router_id]
        
        # Determine operation based on position and LFIB
        if current_router_id == self.source:
            # Ingress: PUSH
            if -1 in router.lfib:
                entry = router.lfib[-1]
                return entry.next_hop, Operation.PUSH, entry.outgoing_label
        elif current_router_id == self.destination:
            # Egress: POP
            if packet.label_stack:
                return None, Operation.POP, None
        else:
            # Transit: SWAP
            if packet.label_stack:
                current_label = packet.label_stack[-1]
                if current_label in router.lfib:
                    entry = router.lfib[current_label]
                    return entry.next_hop, Operation.SWAP, entry.outgoing_label
        
        # Fallback to IP routing if no label
        if current_router_id in router.routing_table:
            next_hop, _ = router.routing_table[packet.destination]
            if next_hop:
                return next_hop, None, None
                
        return None, None, None
        
    def process_packet_hop(self, packet: Packet) -> bool:
        """Process one hop of a packet, return True if completed"""
        if packet.status == PacketStatus.COMPLETED:
            return True
            
        next_hop, operation, new_label = self.get_next_hop_for_packet(packet)
        
        # Store operation for visualization
        packet.operation_at_current = operation
        
        if next_hop is None and packet.current_hop == len(packet.path) - 1:
            # Reached destination
            packet.status = PacketStatus.COMPLETED
            return True
            
        if next_hop is None:
            packet.status = PacketStatus.COMPLETED
            return True
            
        # Apply label operation
        if operation == Operation.PUSH:
            packet.label_stack.append(new_label)
        elif operation == Operation.SWAP and packet.label_stack:
            packet.label_stack[-1] = new_label
        elif operation == Operation.POP and packet.label_stack:
            packet.label_stack.pop()
            
        # Move to next hop
        packet.current_hop += 1
        packet.status = PacketStatus.TRANSITING
        
        return False

# ============================================================================
# GRAPHICS VISUALIZATION
# ============================================================================

class NetworkCanvas(QWidget):
    """Network visualization widget"""
    
    def __init__(self, engine: MPLSEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.main_window = parent
        self.active_packets: Dict[int, Tuple[Packet, float]] = {}  # packet -> progress
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self._animate)
        self.animation_timer.start(30)  # ~33 FPS
        
        self.setMinimumSize(800, 600)
        self.setMouseTracking(True)
        
        # Interaction state
        self.selected_router = None
        self.dragging = False
        self.drag_start = None
        self.creating_link = False
        self.link_start = None
        
    def _animate(self):
        """Animate all active packets"""
        need_update = False
        completed_packets = []
        
        for packet_id, (packet, progress) in self.active_packets.items():
            if packet.status == PacketStatus.COMPLETED:
                completed_packets.append(packet_id)
                continue
                
            if progress >= 1.0:
                # Process this hop
                is_completed = self.engine.process_packet_hop(packet)
                if is_completed:
                    completed_packets.append(packet_id)
                    if self.main_window:
                        self.main_window.log_message(f"✅ Packet {packet.id} delivered to {packet.destination}")
                else:
                    # Reset progress for next hop
                    self.active_packets[packet_id] = (packet, 0.0)
                need_update = True
            else:
                # Continue animation
                self.active_packets[packet_id] = (packet, progress + 0.05)
                need_update = True
                
        for pid in completed_packets:
            del self.active_packets[pid]
            
        if need_update:
            self.update()
            
    def start_packet(self, packet: Packet):
        """Start animating a packet"""
        if packet.id not in self.active_packets:
            packet.status = PacketStatus.TRANSITING
            self.active_packets[packet.id] = (packet, 0.0)
            self.update()
            
    def paintEvent(self, event):
        """Draw everything"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Background
        painter.fillRect(self.rect(), QColor(30, 30, 35))
        
        # Draw links
        for link in self.engine.links:
            r1 = self.engine.routers.get(link.router1)
            r2 = self.engine.routers.get(link.router2)
            if r1 and r2:
                self._draw_link(painter, r1, r2, link.cost)
                
        # Draw routers
        for router in self.engine.routers.values():
            self._draw_router(painter, router)
            
        # Draw temporary link
        if self.creating_link and self.link_start:
            start_r = self.engine.routers.get(self.link_start)
            if start_r and self.drag_start:
                painter.setPen(QPen(QColor(255, 255, 100), 2, Qt.DashLine))
                painter.drawLine(QPointF(start_r.x, start_r.y), self.drag_start)
                
        # Draw packets
        for packet_id, (packet, progress) in self.active_packets.items():
            self._draw_packet(painter, packet, progress)
            
        # Selection highlight
        if self.selected_router:
            router = self.engine.routers.get(self.selected_router)
            if router:
                painter.setPen(QPen(QColor(255, 215, 0), 3))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(router.x, router.y), 35, 35)
                
    def _draw_link(self, painter, r1: Router, r2: Router, cost: int):
        """Draw a link"""
        start = QPointF(r1.x, r1.y)
        end = QPointF(r2.x, r2.y)
        
        painter.setPen(QPen(QColor(100, 100, 120), 2))
        painter.drawLine(start, end)
        
        # Cost label
        mid = QPointF((r1.x + r2.x) / 2, (r1.y + r2.y) / 2)
        painter.setPen(QColor(200, 200, 100))
        painter.setFont(QFont("Arial", 10))
        painter.drawText(QRectF(mid.x() - 15, mid.y() - 10, 30, 20), 
                        Qt.AlignCenter, str(cost))
                        
    def _draw_router(self, painter, router: Router):
        """Draw a router"""
        # Shadow
        painter.setBrush(QBrush(QColor(0, 0, 0, 80)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(router.x + 3, router.y + 3), 30, 30)
        
        # Main circle
        gradient = QRadialGradient(router.x - 5, router.y - 5, 30)
        gradient.setColorAt(0, QColor(70, 130, 200))
        gradient.setColorAt(1, QColor(40, 80, 140))
        painter.setBrush(QBrush(gradient))
        painter.setPen(QPen(QColor(100, 160, 230), 2))
        painter.drawEllipse(QPointF(router.x, router.y), 30, 30)
        
        # Label
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Arial", 12, QFont.Bold))
        painter.drawText(QRectF(router.x - 20, router.y - 10, 40, 20), 
                        Qt.AlignCenter, router.id)
                        
        # Source/destination markers
        if router.id == self.engine.source:
            painter.setPen(QPen(QColor(0, 255, 0), 3))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(router.x, router.y), 35, 35)
        elif router.id == self.engine.destination:
            painter.setPen(QPen(QColor(255, 50, 50), 3))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(router.x, router.y), 35, 35)
            
    def _draw_packet(self, painter, packet: Packet, progress: float):
        """Draw a moving packet"""
        if packet.current_hop >= len(packet.path) - 1:
            return
            
        src_id = packet.path[packet.current_hop]
        dst_id = packet.path[packet.current_hop + 1]
        
        src = self.engine.routers.get(src_id)
        dst = self.engine.routers.get(dst_id)
        
        if src and dst:
            x = src.x + (dst.x - src.x) * progress
            y = src.y + (dst.y - src.y) * progress
            
            # Color based on operation
            if packet.operation_at_current == Operation.PUSH:
                color = QColor(100, 255, 100)  # Green
            elif packet.operation_at_current == Operation.SWAP:
                color = QColor(255, 255, 100)  # Yellow
            elif packet.operation_at_current == Operation.POP:
                color = QColor(255, 100, 100)  # Red
            else:
                color = QColor(255, 200, 50)   # Orange
                
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.drawRect(QRectF(x - 8, y - 6, 16, 12))
            
            # Show label if present
            if packet.label_stack:
                label = packet.label_stack[-1]
                painter.setPen(QColor(0, 0, 0))
                painter.setFont(QFont("Arial", 8, QFont.Bold))
                painter.drawText(QRectF(x - 6, y - 4, 12, 8), 
                                Qt.AlignCenter, str(label))
                                
    def _get_router_at(self, pos):
        """Find router at position"""
        for rid, router in self.engine.routers.items():
            dx = pos.x() - router.x
            dy = pos.y() - router.y
            if math.sqrt(dx*dx + dy*dy) <= 30:
                return rid
        return None
                                
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            router = self._get_router_at(event.pos())
            if router:
                if event.modifiers() & Qt.ControlModifier:
                    self.creating_link = True
                    self.link_start = router
                    self.drag_start = event.pos()
                    self.setCursor(QCursor(Qt.CrossCursor))
                else:
                    self.selected_router = router
                    self.dragging = True
                    self.drag_start = event.pos()
                    self.update()
            else:
                self.selected_router = None
                self.update()
                
    def mouseMoveEvent(self, event):
        if self.dragging and self.selected_router:
            router = self.engine.routers.get(self.selected_router)
            if router:
                dx = event.x() - self.drag_start.x()
                dy = event.y() - self.drag_start.y()
                router.x = max(30, min(self.width() - 30, router.x + dx))
                router.y = max(30, min(self.height() - 30, router.y + dy))
                self.drag_start = event.pos()
                self.update()
        elif self.creating_link:
            self.drag_start = event.pos()
            self.update()
            
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self.creating_link and self.link_start:
                end_router = self._get_router_at(event.pos())
                if end_router and end_router != self.link_start:
                    if self.engine.add_link(self.link_start, end_router):
                        if self.main_window:
                            self.main_window.log_message(f"🔗 Link created: {self.link_start} ↔ {end_router}")
                self.creating_link = False
                self.link_start = None
                self.drag_start = None
                self.setCursor(QCursor(Qt.ArrowCursor))
                self.update()
            self.dragging = False
            
    def contextMenuEvent(self, event):
        router = self._get_router_at(event.pos())
        if router:
            reply = QMessageBox.question(self, 'Remove Router', 
                                       f'Remove router {router}?',
                                       QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.engine.remove_router(router)
                if self.main_window:
                    self.main_window.log_message(f"🗑️ Router {router} removed")
                self.update()

# ============================================================================
# MAIN GUI
# ============================================================================

class MPLSSimulation(QMainWindow):
    """Main application"""
    
    def __init__(self):
        super().__init__()
        self.engine = MPLSEngine()
        self.simulation_active = False
        self.packet_gen_timer = QTimer()
        self.packet_gen_timer.timeout.connect(self.send_single_packet)
        
        self.init_ui()
        self.init_default_network()
        
    def init_ui(self):
        self.setWindowTitle("MPLS Network Simulator")
        self.setGeometry(100, 100, 1400, 800)
        
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QGroupBox { font: bold 14px; color: #fff; border: 2px solid #4a4a4a;
                       border-radius: 8px; margin-top: 10px; padding-top: 10px;
                       background-color: #2d2d2d; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QPushButton { background-color: #4a4a4a; color: white; border: none;
                         padding: 8px; border-radius: 5px; font-weight: bold; }
            QPushButton:hover { background-color: #5a5a5a; }
            QComboBox { background-color: #3a3a3a; color: white; padding: 5px;
                       border-radius: 3px; }
            QTextEdit { background-color: #2d2d2d; color: #00ff00;
                       font-family: monospace; border: 1px solid #4a4a4a; }
            QTableWidget { background-color: #2d2d2d; color: #fff;
                          gridline-color: #4a4a4a; }
            QLabel { color: #fff; }
            QCheckBox { color: #fff; }
        """)
        
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        
        # Left panel - Canvas
        left = QWidget()
        left_layout = QVBoxLayout(left)
        
        info = QLabel("💡 Click router to select | Ctrl+Click & Drag = create link | Right-click = delete | Drag router = move")
        info.setStyleSheet("background: #3a3a3a; padding: 5px; border-radius: 3px;")
        left_layout.addWidget(info)
        
        self.canvas = NetworkCanvas(self.engine, self)
        left_layout.addWidget(self.canvas)
        
        # Right panel - Controls
        right = QWidget()
        right.setFixedWidth(400)
        right_layout = QVBoxLayout(right)
        
        # Network management
        net_group = QGroupBox("Network Management")
        net_layout = QGridLayout()
        
        self.router_combo = QComboBox()
        self.router_combo.addItem("-- Select Router --")
        self.router_combo.currentTextChanged.connect(self.on_router_selected)
        
        self.src_combo = QComboBox()
        self.dst_combo = QComboBox()
        
        add_btn = QPushButton("➕ Add Router")
        add_btn.clicked.connect(self.add_router)
        remove_btn = QPushButton("❌ Remove Selected")
        remove_btn.clicked.connect(self.remove_selected)
        src_btn = QPushButton("🎯 Set as Source")
        src_btn.clicked.connect(self.set_source)
        dst_btn = QPushButton("🏁 Set as Destination")
        dst_btn.clicked.connect(self.set_dest)
        
        net_layout.addWidget(add_btn, 0, 0)
        net_layout.addWidget(remove_btn, 0, 1)
        net_layout.addWidget(self.router_combo, 1, 0, 1, 2)
        net_layout.addWidget(src_btn, 2, 0)
        net_layout.addWidget(dst_btn, 2, 1)
        net_layout.addWidget(QLabel("Source:"), 3, 0)
        net_layout.addWidget(self.src_combo, 3, 1)
        net_layout.addWidget(QLabel("Destination:"), 4, 0)
        net_layout.addWidget(self.dst_combo, 4, 1)
        
        net_group.setLayout(net_layout)
        right_layout.addWidget(net_group)
        
        # Simulation controls
        sim_group = QGroupBox("Simulation")
        sim_layout = QVBoxLayout()
        
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("▶ Start Simulation")
        self.start_btn.clicked.connect(self.start_simulation)
        self.stop_btn = QPushButton("⏹️ Stop")
        self.stop_btn.clicked.connect(self.stop_simulation)
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        sim_layout.addLayout(btn_layout)
        
        gen_layout = QHBoxLayout()
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(1, 10)
        self.rate_spin.setValue(2)
        self.auto_gen = QCheckBox("Auto Generate")
        self.auto_gen.toggled.connect(self.toggle_auto)
        gen_layout.addWidget(QLabel("Rate (pkts/s):"))
        gen_layout.addWidget(self.rate_spin)
        gen_layout.addWidget(self.auto_gen)
        sim_layout.addLayout(gen_layout)
        
        self.send_btn = QPushButton("📦 Send Packet")
        self.send_btn.clicked.connect(self.send_single_packet)
        sim_layout.addWidget(self.send_btn)
        
        sim_group.setLayout(sim_layout)
        right_layout.addWidget(sim_group)
        
        # LFIB View
        lfib_group = QGroupBox("LFIB (Label Forwarding Table)")
        lfib_layout = QVBoxLayout()
        self.lfib_table = QTableWidget()
        self.lfib_table.setColumnCount(4)
        self.lfib_table.setHorizontalHeaderLabels(["In Label", "Operation", "Out Label", "Next Hop"])
        self.lfib_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lfib_layout.addWidget(self.lfib_table)
        lfib_group.setLayout(lfib_layout)
        right_layout.addWidget(lfib_group)
        
        # Routing Table
        rt_group = QGroupBox("Routing Table")
        rt_layout = QVBoxLayout()
        self.rt_table = QTableWidget()
        self.rt_table.setColumnCount(3)
        self.rt_table.setHorizontalHeaderLabels(["Destination", "Next Hop", "Cost"])
        self.rt_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        rt_layout.addWidget(self.rt_table)
        rt_group.setLayout(rt_layout)
        right_layout.addWidget(rt_group)
        
        # Status
        status_group = QGroupBox("Status")
        status_layout = QVBoxLayout()
        self.status_text = QTextEdit()
        self.status_text.setMaximumHeight(150)
        self.status_text.setReadOnly(True)
        status_layout.addWidget(self.status_text)
        status_group.setLayout(status_layout)
        right_layout.addWidget(status_group)
        
        # Splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([1000, 400])
        main_layout.addWidget(splitter)
        
        # Connect signals
        self.engine.routers_updated.connect(self.update_lists)
        self.engine.lfib_updated.connect(self.update_lfib)
        
        self.update_lists()
        
    def on_router_selected(self, router_id: str):
        """Router selection changed"""
        if router_id and router_id != "-- Select Router --":
            self.update_lfib(router_id)
            self.update_routing_table(router_id)
        
    def init_default_network(self):
        """Create default network"""
        self.engine.add_router("R1", 150, 300)
        self.engine.add_router("R2", 400, 200)
        self.engine.add_router("R3", 400, 400)
        self.engine.add_router("R4", 650, 300)
        
        self.engine.add_link("R1", "R2", 1)
        self.engine.add_link("R1", "R3", 2)
        self.engine.add_link("R2", "R4", 1)
        self.engine.add_link("R3", "R4", 1)
        
        self.engine.set_source_destination("R1", "R4")
        
        self.log_message("✅ Default network: R1 (source) → R4 (destination)")
        self.log_message("📡 MPLS LSP configured with PUSH/SWAP/POP operations")
        
    def add_router(self):
        """Add new router"""
        rid = f"R{len(self.engine.routers) + 1}"
        x = random.randint(100, 700)
        y = random.randint(100, 500)
        self.engine.add_router(rid, x, y)
        self.log_message(f"➕ Added {rid}")
        
    def remove_selected(self):
        """Remove selected router"""
        rid = self.router_combo.currentText()
        if rid and rid != "-- Select Router --":
            self.engine.remove_router(rid)
            self.log_message(f"❌ Removed {rid}")
            
    def set_source(self):
        """Set source router"""
        rid = self.router_combo.currentText()
        if rid and rid != "-- Select Router --":
            self.engine.set_source_destination(rid, self.engine.destination)
            self.log_message(f"🎯 Source = {rid}")
            self.update_lists()
            self.canvas.update()
            
    def set_dest(self):
        """Set destination router"""
        rid = self.router_combo.currentText()
        if rid and rid != "-- Select Router --":
            self.engine.set_source_destination(self.engine.source, rid)
            self.log_message(f"🏁 Destination = {rid}")
            self.update_lists()
            self.canvas.update()
            
    def update_lists(self):
        """Update all combo boxes"""
        routers = list(self.engine.routers.keys())
        
        current = self.router_combo.currentText()
        self.router_combo.clear()
        self.router_combo.addItem("-- Select Router --")
        self.router_combo.addItems(routers)
        if current in routers:
            self.router_combo.setCurrentText(current)
        
        self.src_combo.clear()
        self.dst_combo.clear()
        self.src_combo.addItem("None")
        self.dst_combo.addItem("None")
        self.src_combo.addItems(routers)
        self.dst_combo.addItems(routers)
        
        if self.engine.source:
            idx = self.src_combo.findText(self.engine.source)
            if idx >= 0:
                self.src_combo.setCurrentIndex(idx)
        if self.engine.destination:
            idx = self.dst_combo.findText(self.engine.destination)
            if idx >= 0:
                self.dst_combo.setCurrentIndex(idx)
                
    def update_lfib(self, router_id: str):
        """Update LFIB display"""
        current = self.router_combo.currentText()
        if router_id != current and current != "-- Select Router --":
            return
            
        router = self.engine.routers.get(router_id)
        if not router:
            return
            
        self.lfib_table.setRowCount(0)
        for in_label, entry in router.lfib.items():
            row = self.lfib_table.rowCount()
            self.lfib_table.insertRow(row)
            
            in_str = str(in_label) if in_label != -1 else "PUSH"
            self.lfib_table.setItem(row, 0, QTableWidgetItem(in_str))
            self.lfib_table.setItem(row, 1, QTableWidgetItem(entry.operation.value))
            out_str = str(entry.outgoing_label) if entry.outgoing_label else "-"
            self.lfib_table.setItem(row, 2, QTableWidgetItem(out_str))
            self.lfib_table.setItem(row, 3, QTableWidgetItem(entry.next_hop or "-"))
            
    def update_routing_table(self, router_id: str):
        """Update routing table display"""
        router = self.engine.routers.get(router_id)
        if not router:
            return
            
        self.rt_table.setRowCount(0)
        for dest, (next_hop, cost) in router.routing_table.items():
            row = self.rt_table.rowCount()
            self.rt_table.insertRow(row)
            self.rt_table.setItem(row, 0, QTableWidgetItem(dest))
            self.rt_table.setItem(row, 1, QTableWidgetItem(next_hop))
            self.rt_table.setItem(row, 2, QTableWidgetItem(str(cost)))
            
    def start_simulation(self):
        """Start simulation"""
        if not self.engine.source or not self.engine.destination:
            self.log_message("⚠️ Set source and destination first!")
            return
        self.simulation_active = True
        self.log_message("▶ Simulation running")
        
    def stop_simulation(self):
        """Stop simulation"""
        self.simulation_active = False
        self.log_message("⏹️ Simulation stopped")
        
    def send_single_packet(self):
        """Create and send a packet"""
        if not self.simulation_active:
            self.log_message("⚠️ Start simulation first!")
            return
            
        if not self.engine.source or not self.engine.destination:
            self.log_message("⚠️ Set source and destination first!")
            return
            
        packet = self.engine.create_packet()
        if packet:
            self.canvas.start_packet(packet)
            self.log_message(f"📦 Packet {packet.id}: {packet.source} → {packet.destination}")
        else:
            self.log_message("❌ No path available!")
            
    def toggle_auto(self, checked: bool):
        """Toggle auto generation"""
        if checked:
            if not self.engine.source or not self.engine.destination:
                self.log_message("⚠️ Set source/destination first!")
                self.auto_gen.setChecked(False)
                return
            interval = 1000 // self.rate_spin.value()
            self.packet_gen_timer.start(interval)
            self.log_message(f"🔄 Auto-gen enabled ({self.rate_spin.value()} pkts/s)")
        else:
            self.packet_gen_timer.stop()
            self.log_message("⏹️ Auto-gen disabled")
            
    def log_message(self, msg: str):
        """Add status message"""
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.status_text.append(f"[{ts}] {msg}")
        self.status_text.ensureCursorVisible()

def main():
    app = QApplication(sys.argv)
    window = MPLSSimulation()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()