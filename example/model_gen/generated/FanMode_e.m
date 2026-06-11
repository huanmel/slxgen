classdef FanMode_e < Simulink.IntEnumType
  enumeration
    STANDBY (0)
    BOOST   (1)
    AUTO    (2)
    MANUAL  (3)
  end
  methods (Static)
    function retVal = getDefaultValue()
      retVal = FanMode_e.STANDBY;
    end
    function retVal = getStorageType()
      retVal = 'int8';
    end
  end
end
