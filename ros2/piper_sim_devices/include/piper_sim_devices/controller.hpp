#pragma once
#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <optional>
#include <stdexcept>
#include "nankai_robocup_arm_control/motion.hpp"

namespace piper_sim {
struct GripperTarget { double width{}, max_force{}; };
struct Commands {
  std::optional<piper::Joints> arm;
  std::optional<GripperTarget> gripper;
};

// Reuse the upstream pure core. Protocol frames exist only inside this process;
// no Worker, mock feedback, CAN socket, device lock or hardware is instantiated.
class Controller {
  Commands commands_;
  piper::Ns previous_{};
  static std::int32_t integer(const std::uint8_t* data) {
    std::uint32_t bits{};
    for (unsigned i=0; i<4; ++i) bits=(bits<<8)|data[i];
    std::int32_t value; std::memcpy(&value,&bits,4); return value;
  }
  bool send(const can_frame* frames, std::size_t count) {
    piper::Joints q{}; unsigned groups{};
    for (std::size_t i=0; i<count; ++i) {
      const auto& f=frames[i];
      if (f.can_id>=0x155 && f.can_id<=0x157) {
        unsigned g=f.can_id-0x155; groups|=1u<<g;
        q[2*g]=integer(f.data)*piper::pi/180000.;
        q[2*g+1]=integer(f.data+4)*piper::pi/180000.;
      } else if (f.can_id==0x159) {
        commands_.gripper=GripperTarget{integer(f.data)*1e-6,
          ((std::uint16_t(f.data[4])<<8)|f.data[5])*.001};
      }
    }
    if (groups==7) commands_.arm=q;
    return true;
  }
 public:
  piper::Shared shared;
  piper::Control control;
  piper::Motion motion;
  explicit Controller(piper::MotionConfig motion_config={}, piper::Config config={})
    : control(config,shared), motion(motion_config) {}
  void joints(piper::Ns stamp, const piper::Joints& q) {
    if (stamp<=control.snapshot().joints_stamp || stamp<=0) return;
    for (double value:q)
      if (!std::isfinite(value) || std::abs(value*180000/piper::pi)>std::numeric_limits<std::int32_t>::max())
        throw std::invalid_argument("Isaac joint position outside upstream protocol range");
    can_frame status{}; status.can_id=0x2a1; status.can_dlc=8;
    status.data[0]=1; status.data[3]=2;
    control.ingest(status,stamp,stamp);
    auto frames=piper::joint_frames(q);
    for (auto& frame:frames) { frame.can_id+=0x150; control.ingest(frame,stamp,stamp); }
  }
  void gripper(piper::Ns stamp, double width, double force) {
    if (stamp<=control.snapshot().gripper_stamp || stamp<=0) return;
    const double raw=std::nearbyint(force*1000);
    if (!std::isfinite(force) || raw<std::numeric_limits<std::int16_t>::min() ||
        raw>std::numeric_limits<std::int16_t>::max() || !std::isfinite(width) ||
        std::abs(width*1e6)>std::numeric_limits<std::int32_t>::max())
      throw std::invalid_argument("Isaac gripper measurement outside upstream protocol range");
    // gripper_frame encodes unsigned requests; feedback force is signed int16.
    auto frame=piper::gripper_frame(width,0);
    frame.can_id=0x2a8; frame.data[6]=64;
    const auto bits=static_cast<std::uint16_t>(static_cast<std::int16_t>(raw));
    frame.data[4]=bits>>8; frame.data[5]=bits&255;
    control.ingest(frame,stamp,stamp);
  }
  void tick(piper::Ns now) {
    const double dt=previous_ ? std::clamp((now-previous_)*1e-9,0.,.01) : 0.;
    previous_=now;
    piper::Target target;
    if (motion.step(control.snapshot(),dt,target)) shared.targets.put(target);
    control.process(now,[this](const can_frame* f,std::size_t n){return send(f,n);});
  }
  Commands take_commands() { auto result=commands_; commands_={}; return result; }
};

inline double width(double q7,double q8) { return q7-q8; }
// Virtual work: dq7=dw/2, dq8=-dw/2. Positive force opens the gripper.
inline double force(double e7,double e8) { return (e7-e8)/2.; }
}  // namespace piper_sim
