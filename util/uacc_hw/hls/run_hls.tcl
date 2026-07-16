set script_dir [file normalize [file dirname [info script]]]
cd $script_dir

proc configure_solution {} {
    open_solution -reset solution1
    set_part {xc7z020clg400-1}
    create_clock -period 5.0 -name default
}

set compile_flags "-std=c++11"
if {[info exists ::env(UACC_HLS_FRACTION_BITS)]} {
    append compile_flags " -DUACC_HLS_FRACTION_BITS=$::env(UACC_HLS_FRACTION_BITS)"
}
if {[info exists ::env(UACC_HLS_RANGE_PROFILE)]} {
    append compile_flags " -DUACC_HLS_RANGE_PROFILE=$::env(UACC_HLS_RANGE_PROFILE)"
}

set start_stage cost
if {[info exists ::env(UACC_HLS_START_STAGE)]} {
    set start_stage $::env(UACC_HLS_START_STAGE)
}

if {$start_stage == "cost" || $start_stage == "cost_only"} {
    open_project -reset uacc_section14_cost
    set_top uacc_section14_cost
    add_files uacc_section14_cost.cpp -cflags $compile_flags
    add_files -tb uacc_section14_cost_test.cpp -cflags $compile_flags
    configure_solution
    csynth_design
    cosim_design -rtl verilog
    close_project
}

if {$start_stage == "cost" || $start_stage == "collector"} {
    open_project -reset uacc_section14_collector
    set_top uacc_section14_collector
    add_files uacc_section14_collector.cpp -cflags $compile_flags
    add_files -tb uacc_section14_collector_test.cpp -cflags $compile_flags
    configure_solution
    csynth_design
    cosim_design -rtl verilog
    close_project
}

if {$start_stage == "cost" || $start_stage == "allocator"} {
    open_project -reset uacc_section14_allocator
    set_top uacc_section14_allocator
    add_files uacc_section14_cost.cpp -cflags "$compile_flags -DUACC_HLS_CORE_ONLY"
    add_files uacc_section14_allocator.cpp -cflags $compile_flags
    add_files -tb uacc_section14_allocator_test.cpp -cflags $compile_flags
    configure_solution
    csim_design
    csynth_design
    cosim_design -rtl verilog
    close_project
}

exit
