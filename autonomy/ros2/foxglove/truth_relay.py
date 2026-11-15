"""Demo-only: give Foxglove Gazebo's true car pose as its own frame.

/tf_truth carries Gazebo's map -> base_link, which would fight the EKF's
map -> base_link on /tf. Republish it as map -> truth_base_link, and the car
body markers on that frame, so a layout can follow truth without the EKF's
corrections shaking the view.
"""
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from tf2_msgs.msg import TFMessage
from visualization_msgs.msg import MarkerArray

FRAME = 'truth_base_link'


class TruthRelay(Node):
    def __init__(self):
        super().__init__('truth_relay')
        self.tf_pub = self.create_publisher(TFMessage, '/tf', 50)
        # vehicle_viz latches the body once, so both ends must be transient local.
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.body_pub = self.create_publisher(MarkerArray, '/lhr/vehicle/body_truth', latched)
        self.create_subscription(TFMessage, '/tf_truth', self.on_tf, 50)
        self.create_subscription(MarkerArray, '/lhr/vehicle/body', self.on_body, latched)

    def on_tf(self, msg):
        out = TFMessage()
        for t in msg.transforms:
            if t.child_frame_id == 'base_link':
                t.child_frame_id = FRAME
                out.transforms.append(t)
        if out.transforms:
            self.tf_pub.publish(out)

    def on_body(self, msg):
        for m in msg.markers:
            if m.header.frame_id == 'base_link':
                m.header.frame_id = FRAME
            m.ns = m.ns + '_truth'
        self.body_pub.publish(msg)


def main():
    rclpy.init()
    node = TruthRelay()
    node.set_parameters([rclpy.parameter.Parameter('use_sim_time', value=True)])
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass


if __name__ == '__main__':
    main()
