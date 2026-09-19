



## **Hytem Data Protocol Side Scan Sonar SS Series Version V1.0.9** 

# 1. Version Information 

|**Version**|**Date**|**Author**|**Description**|
|---|---|---|---|
|V1.0.0||wyk|Initial version|
|V1.0.4|2021.03.31|wyk|3101 packet modifications:<br>1. Changed the comment on emitBeamWidth to “temporarily<br>unused”<br>u32 emitBeamWidth; //temporarily unused;<br>2. Changed the data type of sampleLength from f32 to u32<br>u32 sampleLength; //sample length|
|V1.0.5|2021.04.02|wyk|3102 packet modification: the output image height and the<br>length of the info data are now both fixed at imageHeight, and<br>no longer vary with lineNumer.|
|V1.0.6|2021.05.06|zjy|107 packet modification: corrected the unit annotations for the<br>high-frequency and low-frequency center frequency|
|V1.0.7|2022.06.16|zjy|Removed the comments on the variable-length parameter<br>portion of the 3101 and 3102 packets|
|V1.0.8|2022.06.21|Wyk|Revised the definition of the longitude and latitude data in the<br>3102 packet.|
|V1.0.8|2022.06.27|zjy|Changed “ms” to “us” in the comments for the high-frequency<br>pulse-length value and low-frequency pulse-length parameter<br>in the 107 packet.|



# 2. Data Description 

- 1) This document's data structures are defined using the C/C++ language. 

- 2) Little-endian mode is used; for multi-byte data, the low-order byte comes first. 

- 3) Adjacent fields must be 4-byte aligned; for example: 

<mark>typedef struct _DefName { u32 frst; u16 second; u16 third; u8  fag; u8  reserved[3]; }DefName;</mark> 

1 



4) Unless necessary, avoid using 8-byte data; if an 8-byte value absolutely must be used, define it in some form as two 4-byte values instead. 

5) Data type definitions: 

1 byte = 8 bits; 

u8: 1-byte unsigned integer; 

u16: 2-byte unsigned integer; u32: 4-byte unsigned integer; 

s8: 1-byte signed integer; 

s16: 2-byte signed integer; 

s32: 4-byte signed integer; 

f32: 4-byte floating-point number. 

<mark>//Variable type defnitions typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef signed char s8; typedef signed short s16; typedef signed int s32; typedef foat f32;</mark> 

6) Time data structure definition: 

<mark>//Time data structure typedef struct _DefTime { u16 year;         //year u8  month;        //month u8  day;          //day u8  hour;         //hour u8  minute;       //minute u8  second;       //second u8  reserved;     //reserved u32 microsecond;  //microsecond }DefTime;  //12 bytes total</mark> 

# 3. Data Packet Composition 

This protocol refers to one complete unit of data of a given type as a “frame” of data; a frame of data may consist of one data packet or multiple data packets. 

Composition of a single packet: 

Packet header + data content (optional, depending on data type) + packet trailer (checksum). 

2 



### 3.1. Packet Header Data Structure 

<mark>//Packet header structure typedef struct _DefPacketHeader { u32 packetIdentifer;  //Packet identifer, fxed at 0x004D5448 // "HTM\0"</mark> u16 headerSize;        //Size of the packet-header structure, in bytes — from packetIdentifier (inclusive) to packetType (inclusive) u16 packetVersion;     //Data format version, Vx.xx: the high byte is x (before the decimal point), the low byte is xx (after it). E.g. V1.01 is represented as 0x0101 u32 packetSize;        //Size of this data packet, in bytes — from packetIdentifier (inclusive) to the checksum (inclusive) <mark>u16 packetFlag;        //Packet fags. //Bit 0: whether this is a multi-packet data frame; 0: single-packet frame; 1: multi-packet frame //Bit 1: whether a checksum is used; 0: no checksum; 1: checksum used //Bits 2-15: reserved, set to 0</mark> u16 identification;    //Frame identifier value; packets belonging to the same frame share the same identifier value, incrementing cyclically from 0 to 65535. u16 totalPacket;       //Total number of packets contained in this frame of data. This value may be ignored when the packet flag indicates a single-packet frame. u16 packetNumber;      //This packet's sequence number within the current frame of data; the first packet is 1, the second is 2, and so on. This value may be ignored when the packet flag indicates a single-packet frame. <mark>u32 packetType;        //Data type — indicates what kind of data the data content holds. } DefPacketHeader;  //24 bytes total</mark> 

### 3.2. Packet Trailer 

<mark>u32 checkSum;</mark> 

Packet trailer. If a checksum is used, checkSum is the sum of all bytes in the packet, from the start of the packet header through the end of the data content; if no checksum is used, checkSum is fixed at 0x77EEEE77. Whether a checksum is used is indicated by bit 1 of the packet flag. 

### 3.3. Data Types (packetType) 

|**Data Type Code**|**Description**|
|---|---|
|107|SS side-scansonarworkcontrolcommand|
|166|Side-scan sonar hardware network connection status packet|
|3101|Side-scansonar intensity data packet|
|3102|Side-scan sonar image data packet|



# 4. Control Command Packet Definition 

### 4.1. Data Type Code 107: SS-Series Side-Scan Sonar Work Control Command, 256 Bytes 

<mark>//SS-series side-scan sonar work control command typedef struct _DefSSSonarWorkPara { DefPacketHeader header;             //Packet header. u32 deviceType;                     //Device model; 20: SS3060</mark> 

3 



|u32 workMode;                       //Work mode; 0: side-scan mode|
|---|
|u32 syncMode;                       //System sync mode; 0: internal sync; 1: external sync; default internal sync|
|u32 syncPolarity;                   //Sync pulse polarity; 0: positive pulse; 1: negative pulse; default positive pulse<br>|
|u32 savs;                           //Surface sound-speed source selection; 0: fxed value set by the main control software; 1:|
|real-time value from a surface sound velocimeter; default 0;|
|f32 soundSpeed;                     //Sound speed used when issuing commands, in m/s — the sound-speed variable used|
|by setting-related parameters, in m/s, range: [1400.000,1600.000]. Default: 1500.00m/s|
|u32 shipSpeedSource;                //Ship-speed source; 0: manual; 1: reserved; 2: INS speed; default 0;<br>|
|f32 shipSpeed;                      //Ship speed, in knots. Default: 5 knots;<br>|
|u32 towfshDepthSource;             //Towfsh depth source; 0: manual; 1: sonar; 2: INS depth; default 1;<br>|
|f32 towfshDepth;                   //Towfsh depth, in meters. Default: 0m;|
|u32 towfshHeightSource;            //Towfsh height source; 0: manual; 1: sonar; 2: INS height above bottom; default<br>|
|1;|
|f32 towfshHeight;                  //Towfsh height, in meters. Default: 0m;|
|u32 towfshHeadSource;              //Towfsh heading source; 0: manual; 1: sonar; 2: INS heading; default 1;|
|f32 towfshHead;                    //Towfsh heading, in degrees. Default: 0°;|
|//High-frequency switch<br>|
|u32 chEnHF;                         //High-frequency switch; 0: of, 1: on;|
|u32 sonarRangeHF;                   //High-frequency range, in meters; one of 10, 15, 25, 50, 75, 100, 150; default 50m;|
|u32 signalTypesHF;                  //High-frequency signal shape; 0 "CW", 1 "LFM"; coupled with range — LFM is|
|prohibited when range is 10 or 15; with the default range of 50m, signal shape defaults to 0 (CW mode);|
|u16 pulseWidthHF;                   //High-frequency pulse length, in us; coupled with range and signal shape; with the|
|default range of 50m and default CW signal shape, the default pulse length is 100us;|
|//1. At a range of 10 or 15, in CW mode, pulse length may be: 0, 15us, 30us, 50us, 100us;|
|//2. At a range of 25, in CW mode, pulse length may be: 0, 15us, 30us, 50us, 100us; in LFM mode: 1000us;<br>//3. At a range of 50 or 75, in CW mode, pulse length may be: 0, 15us, 30us, 50us, 100us; in LFM mode: 1000us,<br>|
|2000us;<br>|
|//4. At a range of 100 or 150, in CW mode, pulse length may be: 0, 15us, 30us, 50us, 100us; in LFM mode: 1000us,|
|<br>2000us, 4000us;<br>|
|u16 pulsesourcelevelHF;             //Pulse source level, default 220dB;|
|<br>u32 centerFreqHF;                   //High-frequency center frequency, in kHz. Default 600kHz;|
|u16 gainHF;                         //High-frequency gain, in dB, 0dB~29dB, in 1 dB steps, default 10dB;|
|u8  reserved1[2];                   //Reserved 1|
|f32 spreadingHF;                    //High-frequency spreading loss, in dB, 10dB~30dB, in 1 dB steps, default 20dB;|
|f32 absorptionHF;                   //High-frequency absorption loss, in dB/km, 10dB/km~400dB/km, in 1 dB/km steps,|
|default 60dB/km;|
|//Low-frequency switch<br>|
|u32 chEnLF;                         //Low-frequency switch; 0: of, 1: on;|
|u32 sonarRangeLF;                   //Low-frequency range, in meters; one of 10, 15, 25, 50, 75, 100, 150, 200, 250, 300;|
|default 50m;|
|u32 signalTypesLF;                  //Low-frequency signal shape; 0 "CW", 1 "LFM"; coupled with range — LFM is|
|prohibited when range is 10 or 15; with the default range of 50m signal shape defaults to 0 (CW mode);|
|,<br>u16 pulseWidthLF;                   //Low-frequency pulse length, in us; coupled with range and signal shape; with the|
|<br>default range of 50m and default CW signal shape, the default pulse length is 0.1ms;|
|<br>//1. At a range of 10 or 15, in CW mode, pulse length may be: 0, 15us, 30us, 50us, 100us;|
|<br>//2. At a range of 25, in CW mode, pulse length may be: 0, 15us, 30us, 50us, 100us; in LFM mode: 1000us;<br>|
|//3. At a range of 50 or 75, in CW mode, pulse length may be: 0, 15us, 30us, 50us, 100us; in LFM mode: 1000us,|
|<br>2000us;|
|//4. At a range of 100, 150, 200, 250, or 300, in CW mode, pulse length may be: 0, 15us, 30us, 50us, 100us; in LFM<br>mode: 1000us, 2000us, 4000us;<br>u16 pulsesourcelevelLF;             //Pulse source level, default 220dB;|



4 



<mark>u32 centerFreqLF;                   //Low-frequency center frequency, in kHz. Default 300kHz; u16 gainLF;                         //Low-frequency gain, in dB, 0dB~39dB, in 1 dB steps, default 10dB; u8  reserved2[2];                   //Reserved 2 f32 spreadingLF;                    //Low-frequency spreading loss, in dB, 10dB~30dB, in 1 dB steps, default 20dB;</mark> f32 absorptionLF;                   //Low-frequency absorption loss, in dB/km, 10dB/km~200dB/km, in 1 dB/km steps, default 30dB/km; <mark>u32 sonarRun;                       //Sonar start-of-operation fag; 0: stopped; 1: running, default 0; u8  reserved3[104];                 //Reserved 3 u32 checkSum;                       //Packet trailer. } DefSSSonarWorkPara;</mark> 

# 5. Data Packet Definitions 

The following provides a detailed description of each data type listed in the Data Types section. 

### 5.1. Data Type Code 166: Side-Scan Sonar Hardware Network Connection Status 

<mark>//Data structure uploaded by the sonar typedef struct _DefDataSideScan { DefPacketHeader header;    //Packet header. u8  sonarState;            //Sonar state //0x00: sonar data and command networks disconnected //0x01: sonar data network connected //0x10: sonar command network connected //0x11: sonar data and command networks connected u32 checkSum;              //Packet trailer. } DefDataSideScan;</mark> 

### 5.2. Data Type Code 3101: Side-Scan Sonar Intensity Data (Fixed Portion: 128 Bytes) 

|//Data structure uploaded by the sonar<br>typedef struct _DefDataSideScan<br>{<br>|
|---|
|DefPacketHeader header;              //Packet header.<br>|
|u8  equipmentType[8];                //Device model, flled in as an ASCII string; if space remains after the string, pad|
|with '\0'. E.g. "ES1000"<br>|
|u8  sonarId[8];                      //Sonar ID, flled in as an ASCII string; if space remains after the string, pad with '\0'.|
|TIME synTime;                        //Sync time  //12 bytes|
|u32 pingNumber;                      //Ping number — the sequence number of each ping|
|u32 sonarRange;                      //Range (maximum detection distance), in cm|
|u32 signalTypes;                     //Indicates the transmitted signal shape, as a level value: 1 = CW; 2 = LFM;|
|u32 emitBeamWidth;                   //Temporarily unused;|
|u32 bandWidth;                       //Bandwidth, in Hz; valid when the signal shape is LFM;|
|u16 pulseWidth;                      //Pulse width, in us<br>|
|u16 pulseSourceLevel;                //Pulse source level, in dB, 175~225dB, 0: of;|
|u32 centerFreq;                      //Signal center frequency, in Hz, e.g. 200000Hz, 400000Hz;|
|u16 gain;                            //Initial gain, in dB;|



5 



<mark>u8  reserved1[2];                    //Reserved 1 f32 spreading;                       //Spreading loss, in dB f32 absorption;                      //Absorption loss, in dB/km f32 soundSpeed;                      //PC sound speed, in m/s f32 sampleRate;                      //Sample rate, in Hz; u32 sampleLength;                    //Sample length u8  reserved2[24];                   //Reserved 2</mark> 

u16 dataValue[2*SampleLength];       //Side-scan data values — 2*SampleLength points total for the left and right sides, ordered as: left-side sample 1 ... left-side sample SampleLength, right-side sample 1 ... right-side sample SampleLength <mark>u32 checkSum;                        //Packet trailer. } DefDataSideScan;</mark> 

5.3. Data Type Code 3102: Side-Scan Sonar Image Data (Fixed Portion: 128 Bytes) 

|typedef struct _DefFLSonarIm<br>{<br>|ageData<br>|
|---|---|
|DefPacketHeader header;<br>|//Packet header.<br>|
|u8  equipmentType[8];<br>pad with '\0'. E.g. "MS200"<br>|//Device model, flled in as an ASCII string; if space remains after the string,<br>|
|u8  sonarId[8];|//Sonar ID, flled in as an ASCII string; if space remains after the string, pad with '\0'.|
|DefTime synTime;|//Ping time — the timestamp of the sonar's most recent ping of data.|
|u32 pingNumber;|//Ping number — the sequence number of the sonar's most recent ping of data.|
|u32 centerFreq;|//Signal center frequency, in kHz — used in dual/multi-frequency systems to|
|distinguish high- and low-fre<br>|quency images<br>|
|u32 imageWidth;|//Image pixel width, must be a multiple of 4; default 1024|
|u32 imageHeight;|//Maximum image pixel height, must be a multiple of 4; default 1024|
|u32 lineNumer;|//Number of valid lines in the current image, [1, imageHeight]|
|f32 imageResolutionX;|//Resolution in the width direction — the length represented by each pixel, in|
|meters<br>||
|u32 imagefag;<br>u8  reserved2[48];<br>//Data section below|//Image attribute fag, reserved; default 0<br>//Reserved|
|u8  DataValue[imageWidth*|imageHeight];       //Single-channel grayscale value for each pixel|
|f32 Latitude[2*imageHeight]|;                 //Coordinate (latitude, in degrees) of the center point of each line.|
|//This is actually a double-t<br>aligned, each double value is|ype array — i.e. double Latitude[imageHeight] — but to keep the structure 4-byte<br>defned as two foat values.|
|f32 Longitude[2*imageHeigh|t];                //Coordinate (longitude, in degrees) of the center point of each line. This is|
|actually a double-type array<br>double value is defned as tw<br>|— i.e. double Longitude[imageHeight] — but to keep the structure 4-byte aligned, each<br>o foat values.<br>|
|u16 Heading[((imageHeight<br>u16 Speed[((imageHeight+1)|+1)/2)*2];          //Heading, in units of 0.01 degrees, [0-35999)<br>/2)*2];            //Speed, in units of 0.01 m/s, [0-65535]|
|u16 Height[((imageHeight+1|)/2)*2];           //Height above bottom, in units of 0.01m, [0-65535]|
|u32 checkSum;|//Packet trailer.|
|} DefFLSonarResultData;||



<mark>//For a 1024×1024 image, this packet's size = 128 + 1024×1024 + 2×4×2×1024 + 3×2×1024 + 4 = 1,071,236 bytes</mark> 

6 

