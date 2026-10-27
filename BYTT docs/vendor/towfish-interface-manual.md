## **Side Scan Sonar Towfish Interface User Manual** 

# 1. Introduction 

As you know, in the past ,you were supposed to communicate with an executible named satcenter to send command and receive data from towfish (hardware) because satcenter was a bridge  between your operatör software and towfish. But from now on the satcenter will reside inside towfish, you will no longer  need satcenter located in your PC running your operator software. That is why, you may assume from now on you are communicating with towfish directly. 

Because of the fact that current satcenter itself can not be embedded inside towfish FPGA hardware due to its size and since satcenter has many reduncdant functions which you will never need, we decided to make it small so that satcenter can easily be embedded inside FPGA board that resides inside towfish. That is why we prepared this document for you. 

Overview of SatCenter software functions: 

SatCenter software serves as the bridge connecting the towfish, the user's software, relaying control commands and sonar data between them. Its main functions include: 

- (1) SatCenter software connects to the towfish, controlling sonar operation and receiving sonar data; 

- (2) SatCenter software can upload the sonar's side-scan data to the user's software; 

### 1.1. System Connection 

#### 1.1.1. Towfish Connection Diagram 

SatCenter software and the user's software will work on two separate systems, satcenter runs inside towfish and operatör software will run n PC. 

- (1) SatCenter software running in the towfish and the user's software running on PC as shown in Figure 1.1 

|**Hardware Connection Diagram**|
|---|
|Towfish|
|IP: 192.168.1.X|
|PC|
|IP: 192.168.1.Y|
|_Figure 1.1  Hardware connection diagram_|



Please use a separate config file for your operatör software to read the Ip adress/port number of both towfish and your PC 

A. IP configuration requirements: 

The towfish,and the PC must all be on the same network segment; 

- B. IP assignment: 

1. The sonar device uses IP 192.168.1.X; 

- 2.The local IP of the PC running the your display-and-control software is 192.168.1.Y; 

#### 1.1.2. System Software Composition 

The system's software consists of the self-storage/forwarding software SatCenter located in the towfish , Your sidescan sonar display-and-control software. 

- (1) SatCenter software and the user's software on seperate system, as shown in Figure 1.3 

**<u><mark>System Software Composition</mark></u>** <u>Self-Storage/Forwarding Software: SatCenter User Software</u> 

_Figure 1.3  System software composition diagram_ 

### 1.2. TCP Network Connections 

The two pieces of software and the sonar communicate with one another using the TCP/IP network protocol. The network connections are shown below: 

The network used by SatCenter software to receive inertial navigation (INS) data supports three network types: UDP, TCP_Client, and TCP_Server. When SatCenter software receives external INS data, only one of these three network types can be used at a time — they cannot be used simultaneously. For details, refer to "2.1. Config.ini Configuration File." 

This section uses the case where SatCenter software and the user's software run on two separate systems as an example. 

##### **(1) IP addresses and port numbers between SatCenter software and the towfish** 

SatCenter software acts as the client, and the sonar device acts as the server, as shown in Table 1.1: 

_Table 1.1  TCP network correspondence — SatCenter software and towfish_ 

|**No.**|**Network Name**|**Server (Sonar) IP in**<br>**config file**|<br>**Listening Port**|**Client (SatCenter**<br>**software) system IP**|**Port**|
|---|---|---|---|---|---|
|1|Control command delivery<br>network|192.168.1.16|5001|192.168.1.16|-|
|2|Sonardatareceivingnetwork|192.168.1.16|5002|192.168.1.16|-|



Note: Since SatCenter software acts as the client, the IP address and port number of the target server it connects to can be changed via the configuration file — see "2. Configuration File Description" for details. 

##### **(2) IP addresses and port numbers between SatCenter software and your side-scan sonar display-and-control software** 

SatCenter software acts as the server, and your software acts as the client, as shown in Table 1.2: 

_Table 1.2  TCP network correspondence — SatCenter software and your software_ 

|**No.**|**Network Name**|**Server (SatCenter**<br>**software) IP in**<br>**config file**|**Listening Port**|**Client (your**<br>**software) system IP**|**Port**|
|---|---|---|---|---|---|
|1|Control command upload<br>network|192.168.1.X|5011|192.168.1.Y|-|
|2|Sonardata uploadnetwork|192.168.1.X|5012|192.168.1.Y|-|
|3|INS data upload network|192.168.1.X|6011|192.168.1.Y|-|



Note: Since SatCenter software acts as the server, the IP address and port it listens on can be changed via the configuration file — see "2. Configuration File Description" for details. 

Port numbers shown above can be used or you can read them from your config file but we strongly recommend you to use these port numbers. 

##### **(3) IP addresses and port numbers between SatCenter software and the user's software** 

SatCenter software acts as the server, and the user's software acts as the client, as shown in Table 1.3: 

_Table 1.3.1  TCP network correspondence — SatCenter software and user software_ 

|**No.**|**Network Name**|**Server (SatCenter**<br>**software) IP in**<br>**config file**|**Listening Port**|**Client (User**<br>**software) system IP**|**Port**|
|---|---|---|---|---|---|
|1|Side-scan sonar work command<br>receiving network|192.168.1.X|16128|192.168.1.Y|-|
|2|Side-scan sonar data upload<br>network|192.168.1.X|16129|192.168.1.Y|-|
|3|INS datareceivingnetwork|192.168.1.X|6003|192.168.1.Y|-|
|4|Raw sonar data upload network|192.168.1.X|16125|192.168.1.Y|-|



Note: Since SatCenter software acts as the server, the IP address and port it listens on can be changed via the configuration file — see "2. Configuration File Description" for details. 

_Table 1.3.2  TCP network correspondence — SatCenter software and user software (roles reversed)_ 

|**No.**|**Network Name**|**Server (User**<br>**software) IP in**<br>**config file**|**Listening Port**|**Client (SatCenter**<br>**software) system IP**|**Port**|
|---|---|---|---|---|---|



Note: Since SatCenter software acts as the client, the IP address and port number of the target server it connects to can be changed via the configuration file — see "2. Configuration File Description" for details. 

_Table 1.3.3  UDP network correspondence — SatCenter software and user software_ 

|**No.**|**Network Name**|**(SatCenter**<br>**software) IP in**<br>**config file**|**Port**|**(User software)**<br>**system IP**|**Port**|
|---|---|---|---|---|---|
|1|INS data delivery network|192.168.1.X|6003|192.168.1.Y|6004<br>(set by<br>user as<br>needed)|



Note: Since SatCenter software acts as the server, the IP address and port it listens on can be changed via the configuration file — see "2. Configuration File Description" for details. 

Special note: When the INS device sends INS data to SatCenter software, only one of the following network types may be used: UDP, TCP_Client, or TCP_Server. For all three network types, the IP address and port number are the INSDataIP and INSDataPort values in the "[SonarDevice]" field of the "Config.ini configuration file." For details, refer to "2.1. Config.ini Configuration File." 

# 2. Configuration File Description 

Running SatCenter software requires two configuration files: Config.ini and Cmd.ini. 

### 2.1. Config.ini Configuration File 

The Config.ini file is used to set the software's operating parameters, including network connection parameters and the device's automatic power-on operating state, as shown in Figure 2.1. 

7 *Config.ini - icG#A SCE(F) $88 (E) *8xt(0) BBV) BBH(H) [example] key=value 

[AutoRun] 

Run=1 



<!-- Start of picture text -->
HydroSonar]<br>mdIP=192.168.1.31<br>mdPort=5011<br>SonarlP=192.168.1.31 SatCenter$r(4-5 Ber<br>SonarPort=5012 ar HydroSonarfii Maite<br>NSDatalP=192.168.1.31<br>NSDataPort=6011<br>UserSoftware]<br>RemoteControllP=192.168.1.31<br>RemoteControlPort=16128<br>DatalP=192.168.1.31 SatCenterfr4-5RP 2+<br>DataPort=16129 — mee<br>serRawDataNetON=0<br>serRawDatalP=192.168.1.31<br>serRawDataPort=16125<br>SonarDevice]<br>mdIP=192.168.1.16 SatCenter#(4-SFalniz<br>—P meee<br>mdPort=5001 Sima<br>SonarlP=192.168.1.16<br>Sonarrort=500<br>INSDataPort=6001 Apa Si<br>[UserPara] HieLEFT<br>DataUploadON=1 ~~~ | co en<br>DataUploadType=3101-—— Mies<br><!-- End of picture text -->

UserRawDataNetUploadType=0 ImageWidth=1024 ImageHeiht=1024 ImageBrightness=50 ImageUploadTime=1000 FileStoreON=1 INSDataTimeSynON=0 INSDataType=0 INSDataNetType=1 TimeZoneHour=8 SyncOutputPeriodSetON=0 SyncOutputPeriodValue= 1000 

[File] 

StoragePathSrc=0 DataFileSavePath=SatCenterData FileMaxSize=1024 AutoDeleteFileON=1 DiskSpaceSize=5 LimitFileSaveSpaceON=0 LimitFileSaveSpaceSize=12 

[Other] UDPMonitorNetON=0 UDPMonitorNetIP=192.168.1.31 UDPMonitorNetPort=6002 

B. 3102 — SatCenter software uploads side-scan sonar image data packets to the user's software; 

C. 3103 — SatCenter software uploads both side-scan sonar intensity data packets and image data packets to the user's software; 

D. 3104 — SatCenter software converts the side-scan sonar data to XTF format and uploads it to the user's software; 

(7) UserRawDataNetUploadType specifies the type of data sent over the raw-data upload network. While the rawdata upload network is enabled (i.e., "UserRawDataNetON" is set to 1): 0 uploads .hsf data packets (raw data); 1 uploads the same data type configured in "DataUploadType." Default: 0. 

(8) ImageWidth: when "DataUploadType" is set to 3102 or 3103, this sets the output image width. 

(9) ImageHeiht (sic): when "DataUploadType" is set to 3102 or 3103, this sets the output image height. 

(10) ImageBrightness: when "DataUploadType" is set to 3102 or 3103, this sets the output image brightness. 

(11) ImageUploadTime: when "DataUploadType" is set to 3102 or 3103, this sets the interval at which image data is uploaded. 

(12) FileStoreON is the switch for .hsf file storage. 1: store; 0: do not store. Default: 1. 

(13) INSDataTimeSynON is the switch for INS data time synchronization. 1: synchronize using the system time; 0: synchronize using the INS data's own timestamp. Default: 0. 

(14) INSDataType selects the INS data protocol type. 0: use the GGA+ZDA data protocol; 1: use Hydro's proprietary INS data protocol. Default: 0. 

(15) INSDataNetType is the network type SatCenter software uses to receive INS data. 0: receive INS data over UDP; 1: receive INS data over TCP_Client; 2: receive INS data over TCP_Server. Default: 1. 

When using the TCP_Client network type, the INSDataIP value in the "[SonarDevice]" field must be set to the IP address used by the INS device. When using the UDP or TCP_Server network type, the INSDataIP value in the "[SonarDevice]" field must be set to the IP address of the system running SatCenter software. When using the UDP network type, it is recommended that the INSDataPort value in the "[SonarDevice]" field not use 6001, but instead use a different port such as 6002, since on some systems UDP port 6001 is already in use. 

(16) TimeZoneHour is the local time-zone value (in hours). Default: Beijing time (set to 8). 

(17) SyncOutputPeriodSetON is the switch for the sync-output period. 0: off (a sync signal is output for every ping); 1: on (sync signals are output at the configured "sync output period"). Default: 0. 

(18) SyncOutputPeriodValue is the sync-output period, in ms. Maximum value: 1600. Default: 1000. 

(20) StoragePathSrc is the source of the custom data-storage path for SatCenter software. 0: data is stored by default in the program's working directory; 1: allows the user to change the storage path. Default: 0. 

(21) DataFileSavePath is the path where SatCenter software stores its data. This path only takes effect when the custom storage path source is set to 1; users can change the data storage location by setting this path. 

For example: with the custom storage path source set to 1, to store data under Windows in the "SatCenterData" directory on drive D, set "DataFileSavePath" in the [File] section directly as DataFileSavePath=D:/SatCenterData. Under Ubuntu, to store data in the "SatCenterData" directory under the home path, set "DataFileSavePath" in the [File] section directly as DataFileSavePath=/home/ubuntu/SatCenterData. 

(22) FileMaxSize is the maximum size of a single file, in MB. Default: 1024 MB — i.e., a new file is created for every 1 GB. 

(23) AutoDeleteFileON is the switch controlling automatic file deletion. 0: automatic file deletion disabled; 1: automatic file deletion enabled. Default: 1. 

(24) DiskSpaceSize is the remaining free-disk-space threshold, in GB. The value must be greater than 0. Default: 5 GB — i.e., when "AutoDeleteFileON" is set to 1 and free disk space falls below 5 GB, the deletion function runs. 

(25) LimitFileSaveSpaceON is the switch controlling the function that caps the maximum disk space used by data files. 0: off; 1: on. Default: 0. Takes effect only when "AutoDeleteFileON" is set to 1. 

(26) LimitFileSaveSpaceSize is the maximum amount of disk space data files may occupy, in GB. This value must be greater than "FileMaxSize." Default: 12. Takes effect only when "LimitFileSaveSpaceON" is set to 1. 

##### **Special note:** 

When "AutoDeleteFileON" is set to 1 (the automatic file-deletion function is enabled) and "LimitFileSaveSpaceON" is also set to 1 (the function capping the maximum disk space used by data files is enabled), the "automatic file deletion" function only runs when both of the following conditions are met: 

① Free disk space is below the "remaining free disk space" threshold. 

② The total size of files in the software's data directory exceeds the "maximum disk space for data files" value. 

##### **Example:** 

Suppose the "remaining free disk space" threshold is set to 5 GB and the "maximum disk space for data files" is set to 12 GB. The "automatic file deletion" function will only run once the system's free disk space is below 5 GB AND the total size of the files in the data directory exceeds 12 GB. If the system's free disk space falls below 5 GB while that size condition is not met, data storage simply stops. 

Another example: suppose the "remaining free disk space" threshold is set to 5 GB while "LimitFileSaveSpaceON" is set to 0 (i.e., the "maximum disk space for data files" function is off). In that case, the "automatic file deletion" function runs whenever free disk space falls below 5 GB. 

(27) UDPMonitorNetON is the switch for the UDP monitoring network. 0: disabled; 1: enabled. Default: 0. For end customers this network is normally left disabled (0). 

(28) UDPMonitorNetIP is the IP address for the UDP monitoring network. Setting this IP correctly is especially important — it must be set to the IP address of the system running SatCenter software. Also make sure the UDP target IP configured on the sonar matches SatCenter's IP. You can use a network utility in TCP_Client mode — set the IP to the sonar's IP and the port to 5001, then send the command $cmd,get,debugip*ff to query the sonar device's configured UDP IP. If the sonar's UDP IP does not match the IP of the system running SatCenter, you can send a command to change the sonar's UDP IP — for example, to change it to 192.168.1.31, send: $cmd,set,debugip,192,168,1,31,6001,6002*ff. After the sonar is restarted, its UDP IP will then be 192.168.1.31. 

Note: this network is generally used for internal debugging by Hydro personnel. 

(29) UDPMonitorNetPort is the port number for the UDP monitoring network. Fixed at 6002, for internal debugging by Hydro personnel. 

Note: while this network is in use, a file named "UDPMonitorNet.txt" is automatically generated under the application's command directory, recording the data received on UDP port 6002. 

### 2.2. Cmd.ini Configuration File 

The Cmd.ini file is used to set the sonar's default operating parameters, as shown in Table 2.1. The sonar's operating parameters consist of two groups: high-frequency and low-frequency parameters. When using a single-frequency side-scan sonar (such as the ES1000 model), the high-frequency parameters are used by default, and the lowfrequency switch must be set to off (ChEn_LF=0)! 

_Table 2.1  Sonar configuration parameters_ 

|**No.**|**Key**|**Description**|
|---|---|---|
|1|UpDataSwitch=0|Data upload selection. 0: result data; 1: raw data; 2: no data uploaded. Default: 0.|



|**No.**|**Key**|**Description**|
|---|---|---|
|2|SurveyMode=0|System survey sync mode. 0: internal sync; 1: external sync, positive pulse; 2:<br>externalsync,negative pulse.Default: 0.|
|3|SoundSpeed=1500.00|Sound velocity. Range: 1400.00–1600.00. Default: 1500.00. Unit: m/s.|
|4|ChEn_HF=1|High-frequency switch. 0: off;1: on.Default:1.|
|5|Range_HF=50|Range. Must be one of: 10, 15, 25, 50, 75, 100, 150. Default: 50. Unit: m.|
|6|SignalShape_HF=0|High-frequency signal shape. 0: CW; 1: LFM (chirp). Signal shape depends on<br>range: LFM cannot be used at a range of 10 or 15 m. With the default range of 50<br>m, the signalshape defaults to 0 (CW).|
|7|PulseWidth_HF=100|High-frequency pulse width. Must be one of: 0, 15, 30, 50, 100, 1000, 2000, 4000.<br>Default: 100. Unit: µs. Pulse width depends on range and signal shape; with the<br>default range of 50 m and default CW signal shape, the default pulse length is 100<br>µs.<br>1. At a range of 10 or 15 m in CW mode, pulse length may be: 15, 30, 50, or 100<br>µs.<br>2. At a range of 25 m in CW mode, pulse length may be: 0, 15, 30, 50, or 1 µs; in<br>LFM mode: 1000 µs.<br>3. At a range of 50 or 75 m in CW mode, pulse length may be: 0, 15, 30, 50, or 1<br>µs; in LFM mode: 1000 or 2000 µs.<br>4. At a range of 100 or 150 m in CW mode, pulse length may be: 0, 15, 30, 50, or<br>100 µs;in LFM mode:1000,2000, or 4000 µs.|
|8<br>9|GainBegin_HF=10<br>Spreading_HF=20.00|High-frequency initial gain. Range: 0–29, in 1 dB steps. Default: 10 dB. Unit: dB.<br> Spreadingloss.Range:10–30,in 1dBsteps.Default:20.00 dB. Unit: dB.|
|10|Absorption_HF=60.00|<sup>Absorption loss. Range: 10–400, in 1 dB/km steps. Default: 60.00 dB/km. Unit:</sup><br>dB/km.|
|11|ChEn_LF=0|Low-frequency switch. 0: off; 1: on. Default: 0.|
|12|Range_LF=50|<br>Low-frequency range. Must be one of: 10, 15, 25, 50, 75, 100, 150, 200, 250, 300.<br>Default: 50. Unit: m.|
|13|SignalShape_LF=0|Low-frequency signal shape. 0: CW; 1: LFM (chirp). Signal shape depends on<br>range: LFM cannot be used at a range of 10 or 15 m. With the default range of 50<br>m, the signalshape defaults to 0 (CW).|
|14|PulseWidth_LF=100|Low-frequency pulse width. Must be one of: 0, 15, 30, 50, 100, 1000, 2000, 4000.<br>Default: 100. Unit: µs. Pulse width depends on range and signal shape; with the<br>default range of 50 m and default CW signal shape, the default pulse length is 100<br>µs.<br>1. At a range of 10 or 15 m in CW mode, pulse length may be: 0, 15, 30, 50, or<br>100 µs.<br>2. At a range of 25 m in CW mode, pulse length may be: 0, 15, 30, 50, or 100 µs;<br>in LFM mode: 1000 µs.<br>3. At a range of 50 or 75 m in CW mode, pulse length may be: 0, 15, 30, 50, or<br>100 µs; in LFM mode: 1000 or 2000 µs.<br>4. At a range of 100, 150, 200, 250, or 300 m in CW mode, pulse length may be:<br>0,15, 30, 50, or 100 µs;in LFM mode:1000,2000, or 4000 µs.|
|15|GainBegin_LF=10|Low-frequency initial gain. Range: 0–39, in 1 dB steps. Default: 10 dB. Unit: dB.|
|16|Spreading_LF=20.00|Spreadingloss.Range:10–30,in 1dBsteps.Default:20.00 dB. Unit: dB.|
|17|Absorption_LF=60.00|<sup>Absorption loss. Range: 10–200, in 1 dB/km steps. Default: 30.00 dB/km. Unit:</sup><br>dB/km.|
|18|SonarRun=0|Sonar start-of-operation flag. 0: stopped; 1: running. Default: 0.|



