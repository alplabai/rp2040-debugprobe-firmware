cmake_minimum_required(VERSION 3.12)

set(CMAKE_BUILD_TYPE RelWithDebInfo)

# Board files for boards the pico-sdk does not ship (e.g. alp_e1m_evk)
list(APPEND PICO_BOARD_HEADER_DIRS ${CMAKE_CURRENT_LIST_DIR}/boards)

include(pico_sdk_import.cmake)

set(FREERTOS_KERNEL_PATH ${CMAKE_CURRENT_LIST_DIR}/freertos)
include(FreeRTOS_Kernel_import.cmake)

project(debugprobe)

pico_sdk_init()

if (${PICO_SDK_VERSION_MAJOR} LESS 2)
	message(SEND_ERROR "Version 2 of the Pico SDK is required to compile this project. Please update your installation at ${PICO_SDK_PATH}")
endif ()

add_executable(debugprobe
        src/probe_config.c
        src/main.c
        src/usb_descriptors.c
        src/probe.c
        src/cdc_uart.c
        src/get_serial.c
        src/sw_dp_pio.c
        src/tusb_edpt_handler.c
        src/autobaud.c
        src/alp_vendor.c
)

target_sources(debugprobe PRIVATE
        CMSIS_DAP/CMSIS/DAP/Firmware/Source/DAP.c
        CMSIS_DAP/CMSIS/DAP/Firmware/Source/JTAG_DP.c
        CMSIS_DAP/CMSIS/DAP/Firmware/Source/SWO.c
        #CMSIS_DAP/CMSIS/DAP/Firmware/Source/SW_DP.c
        )

target_include_directories(debugprobe PRIVATE
        CMSIS_DAP/CMSIS/DAP/Firmware/Include/
        CMSIS_DAP/CMSIS/Core/Include/
        include/
        )

target_compile_options(debugprobe PRIVATE -Wall)

pico_generate_pio_header(debugprobe ${CMAKE_CURRENT_LIST_DIR}/src/probe.pio)
pico_generate_pio_header(debugprobe ${CMAKE_CURRENT_LIST_DIR}/src/probe_oen.pio)
pico_generate_pio_header(debugprobe ${CMAKE_CURRENT_LIST_DIR}/src/autobaud.pio)

target_include_directories(debugprobe PRIVATE src)

# add version
add_custom_target(version
        ${CMAKE_SOURCE_DIR}/get-version.sh
        WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
)

target_include_directories(debugprobe PRIVATE
        ${CMAKE_BINARY_DIR}/generated
        )

add_dependencies(debugprobe version)

target_compile_definitions (debugprobe PRIVATE
	PICO_RP2040_USB_DEVICE_ENUMERATION_FIX=1
)

option (DEBUG_ON_PICO "Compile firmware for the Pico instead of Debug Probe" OFF)
if (DEBUG_ON_PICO)
    target_compile_definitions (debugprobe PRIVATE
	DEBUG_ON_PICO=1
    )
    if (PICO_BOARD STREQUAL "pico")
        set_target_properties(debugprobe PROPERTIES
            OUTPUT_NAME "debugprobe_on_pico"
        )
    elseif (PICO_BOARD STREQUAL "pico2")
        set_target_properties(debugprobe PROPERTIES
            OUTPUT_NAME "debugprobe_on_pico2"
        )
    else ()
        message(SEND_ERROR "Unsupported board ${PICO_BOARD}")
    endif ()
endif ()

option (ALP_E1M_EVK "Compile firmware for the on-board probe of the Alp Lab E1M EVK 2641-R3" OFF)
if (ALP_E1M_EVK)
    if (DEBUG_ON_PICO)
        message(SEND_ERROR "ALP_E1M_EVK and DEBUG_ON_PICO are exclusive")
    endif ()
    if (NOT PICO_BOARD STREQUAL "alp_e1m_evk")
        message(SEND_ERROR "ALP_E1M_EVK needs -DPICO_BOARD=alp_e1m_evk")
    endif ()
    target_compile_definitions (debugprobe PRIVATE
	ALP_E1M_EVK=1
    )
    set_target_properties(debugprobe PROPERTIES
        OUTPUT_NAME "debugprobe_alp_e1m_evk"
    )
    # PROBE_SERIAL_IN_FLASH: the USB serial record lives in the last flash sector.
    target_link_libraries(debugprobe PRIVATE hardware_flash pico_rand)
    # Implicit linker script (added to the default one): fails the link if the
    # image reaches that sector.
    target_link_options(debugprobe PRIVATE ${CMAKE_CURRENT_LIST_DIR}/src/serial_sector.ld)
    set_property(TARGET debugprobe APPEND PROPERTY LINK_DEPENDS ${CMAKE_CURRENT_LIST_DIR}/src/serial_sector.ld)
endif ()


target_link_libraries(debugprobe PRIVATE
        pico_multicore
        pico_stdlib
        pico_unique_id
        tinyusb_device
        tinyusb_board
        hardware_pio
        hardware_i2c
        hardware_adc
        hardware_dma
        hardware_irq
        hardware_clocks
        FreeRTOS-Kernel
        FreeRTOS-Kernel-Heap1
)

pico_set_binary_type(debugprobe copy_to_ram)

pico_add_extra_outputs(debugprobe)
