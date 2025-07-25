import rclpy
from rclpy.node import Node                     # use this to tell ROS2 that this is a ROS2 node
from sensor_msgs.msg import PointCloud2         # needed for interpreting the pointcloud messages
import sys
import math     #used for IsNaN / IsInf
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs_py import point_cloud2         # needed for interpreting the pointcloud messages
import hashlib                                  # used to compare messages to previous messages

from lidar_interfaces.msg import LidarAlert     # From [the name of the package] import [the name of the message type]

sys.stdout.reconfigure(line_buffering=True)

# ideal values for the pointcloud / parameters for the pointcloud
ideal_point_count = 230400                      # ideal point count for one message
ideal_message_rate = 10                         # ideal frequency, in hz
maximum_range = 200                             # maximum range, in meters, according to the Pandar website


# how far from expected values can we deviate?
# note that 0.01 means 1%, not 0.01% (for example)
percent_point_count_deviation_allowed = 0.001   # % deviation allowed from the ideal point count
percent_invalid_point_count_allowed = 0.001     # what portion of points can be invalid before we flag the message
percent_exceptional_point_count_allowed = 0.01  # what portion of points can be outside the maximum range before we flag the message
percent_message_rate_deviation_allowed = 0.2    # 20% of the ideal hz in either direction before flag



max_points = ideal_point_count * (1 + percent_point_count_deviation_allowed)
min_points = ideal_point_count * (1 - percent_point_count_deviation_allowed)


class PointCloudChecker(Node):
    def __init__(self):
        super().__init__('pointcloud_checker')
        
        self.subscription = self.create_subscription(
            PointCloud2,
            '/pointcloud',
            self.listener_callback,
            qos_profile_sensor_data
)
        self.subscription  
        self.publisher = self.create_publisher(LidarAlert, 'LidarLogs', 10)     # Message Type, name of topic to send to, queue depth
        self.prev_msg_time = None
        self.prev_data_hash = None

    def listener_callback(self, msg):
        self.check_data(msg)
        pass

    def check_data(self, msg):

        # Hierarchy of priority:
        # What is the frequency?
        # Is the message empty?
        # Is the message identical to the previous message?
        # Are there too many or too few points?
        # Are the points valid?
        # Are the points within the expected range?

        # Update the current hash, time, and number of points before anything else
        current_hash = hashlib.md5(msg.data).hexdigest()
        msg_time = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        num_points = msg.width * msg.height     # number of points per ring * number of rings

        # First thing is if the frequency is right. It's the only data we can use even when the message is empty.
        if self.prev_msg_time is not None:
            time_diff = msg_time - self.prev_msg_time
            missed_messages = round(time_diff * ideal_message_rate) -1 if time_diff > 0 else 0
            frequency = (1+missed_messages) / time_diff if time_diff > 0 else 0     # Number of messages / time difference = frequency

            # If the frequency delta is more than the delta allowed
            if abs(frequency-ideal_message_rate) > ideal_message_rate * percent_message_rate_deviation_allowed: #
                self.publisher.publish(LidarAlert(
                level=b"3", # ERROR level
                error_name="Message Rate Deviation",
                description=f"Expected ~{ideal_message_rate:.2f}Hz, got {frequency:.2f}Hz"
                ))        

            if missed_messages > 0 and missed_messages < 10:
                self.publisher.publish(LidarAlert(
                level=b"2", # WARN level
                error_name="Missed Messages",
                description=f"This node missed {missed_messages} messages in a row. Maybe just a hiccup."
                ))
            if missed_messages>=10:
                self.publisher.publish(LidarAlert(
                level=b"3", # ERROR level
                error_name="Missed Many Messages",
                description=f"This node missed {missed_messages} messages in a row, around {missed_messages * 0.1} seconds. This is a serious issue."
                ))

        # Save the current time as the new "past" time
        self.prev_msg_time = msg_time
        
        # we want to filter the message so if it's empty or identical, we don't go through the whole process.
        # FATAL error if no points are found
        if num_points == 0:
            self.publisher.publish(LidarAlert(
                level=b"4", # FATAL level
                error_name="Empty Pointcloud",
                description=f"Received PointCloud2 message with zero points"
            ))
            return

        # Hashes the current and previous message, compares them.
        # Error if identical -- almost 100% likely that a glitch has happened    
        if self.prev_data_hash == current_hash:
            self.publisher.publish(LidarAlert(
            level=b"3", # ERROR level
            error_name="Identical Frame",
            description=f"Received a frame identical to the previous"
            ))
            return 
        self.prev_data_hash = current_hash    
        
        if num_points < min_points:
            self.publisher.publish(LidarAlert(
            level=b"3", # ERROR level
            error_name="Low Point Count",
            description=f"Received {num_points} points, below expected minimum of {min_points}"
            ))
                
        if num_points > max_points:
            self.publisher.publish(LidarAlert(
            level=b"2", # WARN level
            error_name="High Point Count",
            description=f"Received {num_points} points, above expected maximum of {max_points}"
            ))

        # preparing for the For loop
        exceptional_count = 0
        invalid_count = 0
            
        for point in point_cloud2.read_points(msg, field_names=("x", "y", "z"), skip_nans=False):
            x, y, z = point

            # Check if any coordinate is NaN or Inf
            if not all(math.isfinite(v) for v in (x, y, z)):
                invalid_count += 1
                
            # Check if the point is within the maximum range using pythagorean theorem
            distance = math.sqrt(x*x + y*y + z*z)
            if distance > maximum_range:
                exceptional_count += 1
                    
                
                # If the ratio of exceptional to total is greater than the percent allowed, we publish a warning
        if (exceptional_count / num_points) > percent_exceptional_point_count_allowed:
                self.publisher.publish(LidarAlert(
                level=b"2", # WARN level
                error_name="Exceptional Point Count",
                description=f"Received {exceptional_count} points outside the maximum range of {maximum_range}."
                ))
                    
                # If the ratio of invalid to total is greater than the percent allowed, we publish an error alert
        if (invalid_count / num_points) > percent_invalid_point_count_allowed:
                self.publisher.publish(LidarAlert(
                level=b"3", # ERROR level
                error_name="Invalid Point Count",
                description=f"Received {invalid_count} invalid points"
                ))
        return

    
def main(args=None):
    print("Starting PointCloudChecker Node")
    rclpy.init(args=args)
    node = PointCloudChecker()

    print("Node initialized, spinning now.")
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()
    print("PointCloudChecker Node has been shut down.")
