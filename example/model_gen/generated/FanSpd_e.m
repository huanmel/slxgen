classdef FanSpd_e < Simulink.IntEnumType
  enumeration
    OFF  (0)
    LOW  (1)
    MED  (2)
    HIGH (3)
  end
  methods (Static)
    function retVal = getDefaultValue()
      retVal = FanSpd_e.OFF;
    end
    function retVal = getStorageType()
      retVal = 'int8';
    end
  end
end
