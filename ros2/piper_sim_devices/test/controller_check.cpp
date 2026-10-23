#include <iostream>
#include "piper_sim_devices/controller.hpp"
using namespace piper_sim;
static void require(bool condition,const char* reason) { if (!condition) throw std::runtime_error(reason); }
struct Fixture {
  Controller controller;
  piper::Ns now{1000000000};
  piper::Joints q{0,.2,-.2,0,0,0};
  Fixture() { sample(.1,0); }
  void sample(double width,double force) {
    now+=10000000; controller.joints(now,q); controller.gripper(now,width,force); controller.tick(now);
  }
  void start(double width,double force) {
    controller.shared.gripper_state.store(1); controller.shared.gripper_start.put({1,width,force});
    controller.tick(++now);
  }
  void stable(double width,double force,int count=51) { for (int i=0;i<count;++i) sample(width,force); }
  piper::Reply result() { piper::Reply result; require(controller.shared.gripper_result.take(result),"missing result"); return result; }
  void pending() { piper::Reply result; require(!controller.shared.gripper_result.take(result),"unexpected completion"); }
};
int main() {
  try {
    require(std::abs(width(.035,-.035)-.07)<1e-12,"measured width");
    require(force(-5,5)==-5 && force(2,-2)==2,"force sign and virtual work");
    Fixture contact; contact.start(0,5);
    auto command=contact.controller.take_commands();
    require(command.gripper && command.gripper->width==0 && command.gripper->max_force==5,"command mapping");
    contact.stable(.07,-2,50); contact.pending();
    for(int i=0;i<100;++i) { contact.controller.gripper(contact.now,.07,-2); contact.controller.tick(contact.now); }
    contact.pending(); contact.sample(.07,-2);
    auto result=contact.result();
    require(result.success && result.completion==piper::Completion::ContactStop && result.force==-2,"physical contact completion");
    require(!contact.controller.take_commands().gripper,"success must retain target without another command");
    Fixture empty; empty.start(0,5); empty.stable(0,0);
    require(empty.result().completion==piper::Completion::WidthReached,"empty closing differs from contact");
    Fixture wrong; wrong.start(0,5); wrong.stable(.07,2); wrong.pending();
    wrong.controller.shared.gripper_state.store(3); wrong.controller.tick(wrong.now);
    require(wrong.result().completion==piper::Completion::None,"cancel during paused time");
    auto hold=wrong.controller.take_commands().gripper;
    require(hold && std::abs(hold->width-.07)<1e-12 && hold->max_force==5,"cancel holds measured width and original force");
    Fixture blocked; blocked.sample(.075,0); blocked.start(.1,1); blocked.stable(.08,1);
    require(!blocked.result().success,"blocked opening cannot succeed");
    Fixture motion;
    auto desired=motion.q; desired[0]+=.02;
    piper::Reference reference; reference.command=0; reference.valid=true; reference.target=piper::pose(piper::tcp_fk(desired));
    motion.controller.motion.reference(reference); motion.sample(.1,0);
    require(motion.controller.take_commands().arm.has_value(),"TCP reference produces joint command");
    require(!motion.controller.motion.snapshot().converged,"actual feedback controls convergence");
    motion.controller.tick(motion.now+10000000);
    require(!motion.controller.take_commands().arm,"missing new feedback stops integration");
    bool rejected=false;
    try { motion.controller.gripper(motion.now+1,.07,-33.); } catch(const std::invalid_argument&) { rejected=true; }
    require(rejected,"force conversion must not overflow signed protocol");
    std::cout<<"Isaac core: force/width mapping, contact, cancellation, clock and measured TCP checks passed\n";
    return 0;
  } catch(const std::exception& error) { std::cerr<<error.what()<<'\n'; return 1; }
}
