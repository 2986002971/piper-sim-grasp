#include <array>
#include <chrono>
#include <optional>
#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "control_msgs/msg/gripper_command.hpp"
#include "diagnostic_msgs/msg/diagnostic_array.hpp"
#include "sensor_msgs/msg/joint_state.hpp"
#include "nankai_robocup_interfaces/action/move_gripper.hpp"
#include "nankai_robocup_interfaces/msg/tcp_reference.hpp"
#include "nankai_robocup_interfaces/msg/tcp_tracking_state.hpp"
#include "piper_sim_devices/controller.hpp"

namespace piper_sim {
using JointState=sensor_msgs::msg::JointState;
using Reference=nankai_robocup_interfaces::msg::TcpReference;
using Tracking=nankai_robocup_interfaces::msg::TcpTrackingState;
using Gripper=nankai_robocup_interfaces::action::MoveGripper;
using Handle=rclcpp_action::ServerGoalHandle<Gripper>;

class Driver : public rclcpp::Node {
  std::unique_ptr<Controller> core_;
  double max_width_{};
  piper::Ns received_{}, published_{}, last_diagnostic_{};
  bool valid_gripper_{};
  JointState actual_;
  std::shared_ptr<Handle> goal_;
  bool pending_{};
  std::uint64_t request_id_{};
  rclcpp::Publisher<JointState>::SharedPtr arm_command_, joints_, gripper_;
  rclcpp::Publisher<control_msgs::msg::GripperCommand>::SharedPtr gripper_command_;
  rclcpp::Publisher<Tracking>::SharedPtr tracking_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diagnostics_;
  rclcpp::Subscription<JointState>::SharedPtr measured_;
  rclcpp::Subscription<Reference>::SharedPtr references_;
  rclcpp_action::Server<Gripper>::SharedPtr action_;
  rclcpp::TimerBase::SharedPtr timer_;

  template<class T> T parameter(const char* name,T value) {
    rcl_interfaces::msg::ParameterDescriptor description;
    description.read_only=true;
    return declare_parameter<T>(name,value,description);
  }
  void measured(const JointState& message) {
    const auto stamp=rclcpp::Time(message.header.stamp).nanoseconds();
    if (stamp<=received_) return;
    std::array<std::size_t,8> indices{};
    for (unsigned i=0;i<8;++i) {
      const auto name="joint"+std::to_string(i+1);
      auto it=std::find(message.name.begin(),message.name.end(),name);
      if (it==message.name.end() || std::count(message.name.begin(),message.name.end(),name)!=1 ||
          std::size_t(it-message.name.begin())>=message.position.size()) {
        RCLCPP_ERROR_THROTTLE(get_logger(),*get_clock(),2000,"Isaac feedback requires eight measured joints");
        return;
      }
      indices[i]=it-message.name.begin();
      if (!std::isfinite(message.position[indices[i]])) return;
    }
    JointState sample; sample.header=message.header;
    for (auto i:indices) { sample.name.push_back(message.name[i]); sample.position.push_back(message.position[i]); }
    for (auto field:{&JointState::velocity,&JointState::effort})
      if ((message.*field).size()==message.name.size())
        for (auto i:indices) (sample.*field).push_back((message.*field)[i]);
    piper::Joints q; std::copy_n(sample.position.begin(),6,q.begin());
    core_->joints(stamp,q);
    valid_gripper_=false;
    if (sample.effort.size()==8) {
      try {
        core_->gripper(stamp,width(sample.position[6],sample.position[7]),force(sample.effort[6],sample.effort[7]));
        valid_gripper_=true;
      } catch (const std::invalid_argument& error) {
        RCLCPP_ERROR_THROTTLE(get_logger(),*get_clock(),2000,"%s",error.what());
      }
    } else {
      RCLCPP_ERROR_THROTTLE(get_logger(),*get_clock(),2000,"Measured gripper efforts unavailable; no force samples generated");
    }
    actual_=std::move(sample); received_=stamp;
  }
  void reference(const Reference& message) {
    const auto& p=message.target_pose;
    piper::Reference r;
    r.command=message.command; r.stamp=rclcpp::Time(message.header.stamp).nanoseconds();
    r.target={{p.position.x,p.position.y,p.position.z},
              {p.orientation.x,p.orientation.y,p.orientation.z,p.orientation.w}};
    r.valid=r.command==Reference::HOLD || (r.command==Reference::TRACK &&
      message.header.frame_id=="piper_base_link" && piper::valid_pose(r.target));
    core_->motion.reference(r);
  }
  void tick() {
    const auto now=get_clock()->now().nanoseconds();
    core_->tick(std::max(now,received_));
    auto commands=core_->take_commands();
    if (commands.arm) {
      JointState command; command.header.stamp=get_clock()->now();
      for (unsigned i=0;i<6;++i) command.name.push_back("joint"+std::to_string(i+1));
      command.position.assign(commands.arm->begin(),commands.arm->end()); arm_command_->publish(command);
    }
    if (commands.gripper) {
      control_msgs::msg::GripperCommand command;
      command.position=commands.gripper->width; command.max_effort=commands.gripper->max_force;
      gripper_command_->publish(command);
    }
    const auto& state=core_->control.snapshot();
    if (received_>published_) {
      // Keep the two actual finger positions, including asymmetric contact.
      JointState arm; arm.header=actual_.header;
      arm.name.assign(actual_.name.begin(),actual_.name.begin()+6);
      arm.position.assign(actual_.position.begin(),actual_.position.begin()+6); joints_->publish(arm);
      JointState fingers; fingers.header=actual_.header;
      fingers.name={"joint7","joint8"}; fingers.position={actual_.position[6],actual_.position[7]}; joints_->publish(fingers);
      if (valid_gripper_) {
        JointState grip; grip.header=actual_.header; grip.name={"gripper"};
        grip.position={state.width}; grip.effort={state.force}; gripper_->publish(grip);
      }
      published_=received_;
    }
    const auto& motion=core_->motion.snapshot();
    Tracking tracking;
    tracking.reference.header.frame_id="piper_base_link";
    tracking.reference.header.stamp=rclcpp::Time(motion.reference.stamp);
    tracking.reference.command=motion.reference.command;
    auto& p=tracking.reference.target_pose;
    p.position.x=motion.reference.target.position[0]; p.position.y=motion.reference.target.position[1]; p.position.z=motion.reference.target.position[2];
    p.orientation.x=motion.reference.target.orientation[0]; p.orientation.y=motion.reference.target.orientation[1];
    p.orientation.z=motion.reference.target.orientation[2]; p.orientation.w=motion.reference.target.orientation[3];
    tracking.measurement_stamp=rclcpp::Time(state.joints_stamp);
    tracking.health=state.health==piper::Health::Fault ? Tracking::FAULT : Tracking::READY;
    tracking.converged=motion.converged;
    tracking.translation_error=motion.translation_error; tracking.rotation_error=motion.rotation_error;
    tracking.reason=piper::describe(motion.reason); tracking_->publish(tracking);
    piper::Reply reply;
    if (core_->shared.gripper_result.take(reply) && goal_ && reply.id==request_id_) {
      auto result=std::make_shared<Gripper::Result>();
      result->success=reply.success; result->final_width_m=reply.width; result->final_force_n=reply.force;
      result->completion_type=static_cast<std::uint8_t>(reply.completion); result->reason=piper::describe(reply.error);
      if (goal_->is_canceling()) goal_->canceled(result);
      else if (reply.success) goal_->succeed(result);
      else goal_->abort(result);
      goal_.reset(); core_->shared.gripper_state.store(0);
    }
    if (goal_) {
      auto feedback=std::make_shared<Gripper::Feedback>(); feedback->width_m=state.width; feedback->force_n=state.force;
      goal_->publish_feedback(feedback);
    }
    const auto wall=piper::monotonic_now();
    if (wall-last_diagnostic_>=100000000) {
      last_diagnostic_=wall;
      diagnostic_msgs::msg::DiagnosticStatus status;
      status.name="piper_sim/driver"; status.hardware_id="Isaac PhysX";
      status.level=received_ && valid_gripper_ ? 0 : 1;
      status.message=received_ ? (valid_gripper_ ? tracking.reason : "waiting for measured gripper effort") : "waiting for Isaac feedback";
      diagnostic_msgs::msg::DiagnosticArray diagnostics; diagnostics.header.stamp=get_clock()->now();
      diagnostics.status.push_back(status); diagnostics_->publish(diagnostics);
    }
  }
 public:
  Driver():Node("isaac_arm_driver") {
    piper::MotionConfig motion;
    motion.gain=parameter("gain",motion.gain);
    motion.max_linear_speed=parameter("max_linear_speed",motion.max_linear_speed);
    motion.max_angular_speed=parameter("max_angular_speed",motion.max_angular_speed);
    motion.translation_tolerance=parameter("translation_tolerance",motion.translation_tolerance);
    motion.rotation_tolerance=parameter("rotation_tolerance",motion.rotation_tolerance);
    for (double v:{motion.gain,motion.max_linear_speed,motion.max_angular_speed,motion.translation_tolerance,motion.rotation_tolerance})
      if (!std::isfinite(v) || v<=0) throw std::invalid_argument("positive finite motion parameters required");
    max_width_=parameter("gripper_max_width_m",.1);
    if (!std::isfinite(max_width_) || max_width_<=0 || max_width_>.1)
      throw std::invalid_argument("Piper simulation gripper stroke is 0.1 m");
    piper::Config config; config.gripper_max_width=max_width_;
    core_=std::make_unique<Controller>(motion,config);
    auto qos=rclcpp::QoS(1).reliable();
    arm_command_=create_publisher<JointState>(parameter<std::string>("bridge_command_topic","/isaac/piper/joint_command"),qos);
    gripper_command_=create_publisher<control_msgs::msg::GripperCommand>(parameter<std::string>("bridge_gripper_command_topic","/isaac/piper/gripper_command"),qos);
    joints_=create_publisher<JointState>("joint_states",rclcpp::QoS(2));
    gripper_=create_publisher<JointState>("gripper_state",qos);
    tracking_=create_publisher<Tracking>("state",qos);
    diagnostics_=create_publisher<diagnostic_msgs::msg::DiagnosticArray>("diagnostics",10);
    measured_=create_subscription<JointState>(parameter<std::string>("bridge_feedback_topic","/isaac/piper/joint_states"),rclcpp::SensorDataQoS(),[this](JointState::ConstSharedPtr m){measured(*m);});
    references_=create_subscription<Reference>("reference",qos,[this](Reference::ConstSharedPtr m){reference(*m);});
    action_=rclcpp_action::create_server<Gripper>(this,"move_gripper",
      [this](const rclcpp_action::GoalUUID&,std::shared_ptr<const Gripper::Goal> request){
        if (goal_ || pending_ || !valid_gripper_ || !piper::encodable_gripper(request->width_m,request->force_n) || request->width_m>max_width_)
          return rclcpp_action::GoalResponse::REJECT;
        pending_=true; return rclcpp_action::GoalResponse::ACCEPT_AND_EXECUTE;
      },
      [this](const std::shared_ptr<Handle> h){
        if (goal_!=h || (core_->shared.gripper_state.load()!=1 && core_->shared.gripper_state.load()!=2))
          return rclcpp_action::CancelResponse::REJECT;
        core_->shared.gripper_state.store(3); return rclcpp_action::CancelResponse::ACCEPT;
      },
      [this](const std::shared_ptr<Handle> h){
        goal_=h; pending_=false; core_->shared.gripper_state.store(1);
        core_->shared.gripper_start.put({++request_id_,h->get_goal()->width_m,h->get_goal()->force_n});
      });
    // Dispatch/cancel while paused; integration and observation use simulation time.
    timer_=create_wall_timer(std::chrono::milliseconds(10),[this]{tick();});
  }
};
}  // namespace piper_sim

int main(int argc,char** argv) {
  rclcpp::init(argc,argv);
  try { rclcpp::spin(std::make_shared<piper_sim::Driver>()); }
  catch (const std::exception& error) { fprintf(stderr,"Isaac arm driver: %s\n",error.what()); rclcpp::shutdown(); return 1; }
  rclcpp::shutdown(); return 0;
}
